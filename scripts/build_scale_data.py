"""WP8 确定性 SQL 侧规模数据生成器（E7/F6：效率与伸缩性证明）。

它只做三件事，全部可离线复现：

1. 生成**同一 schema** 的三档规模数据（1 千 / 1 万 / 10 万 `order_items` 行），字段集取自
   演示库 `deploy/init/03_demo_retail.sql` 的**最小子集**（`products` / `customers` / `orders` /
   `order_items`）。省略的字段与原因写在 manifest 的 `omitted_fields` 里，不假装等价。
2. 只写 **side 目录**（默认 `tmp/scale_v2_data`）：不写 `datamind_meta`，不碰 `demo_retail`，
   不碰任何演示库。生成物是可删的临时件。
3. 自带 `--check`：把数据重新生成到**临时目录**，与磁盘上的 manifest 逐表比对行数与
   sha256，并校验「小档是大档的前缀」（嵌套属性）。不一致则退出码 1。

确定性来自一个自实现的 splitmix64 流（不依赖 `random` 模块的实现细节），每一行的内容只由
行号决定，因此**任何档位的行序列都是同一基础序列的前缀**，同一档位重复生成得到逐字节相同的
文件（`--check` 就是在验证这一点）。

用法:
    python scripts/build_scale_data.py                      # 生成三档到 tmp/scale_v2_data
    python scripts/build_scale_data.py --check              # 只读校验（不写总目录）
    python scripts/build_scale_data.py --tiers 200,400 --out tmp/x
    python scripts/build_scale_data.py --clean              # 只删自己生成的目录（有守卫）
    python scripts/build_scale_data.py --emit-schema        # 打印建表 SQL（供临时库使用）
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "tmp" / "scale_v2_data"
TIERS = (1000, 10000, 100000)
GENERATOR = "build_scale_data.py/v1"
SCHEMA_VERSION = "retail-min-v1"
SEED = 20261010
#: 只在**我们自己生成的**目录里出现，`--clean` 依赖它才敢删。
MARKER = "datamind-scale-probe"

#: 演示库 `deploy/init/03_demo_retail.sql` 的最小字段子集。刻意**不带二级索引**：规模实验的
#: 「优化前」就是「只有主键」的状态，索引对照组由 `PRODUCT_INDEX_DDL` 显式加上（可回退）。
SCHEMA_SQL = """
CREATE TABLE products (
    product_id   SERIAL PRIMARY KEY,
    product_name VARCHAR(128) NOT NULL,
    category     VARCHAR(64)  NOT NULL,
    brand        VARCHAR(64),
    list_price   NUMERIC(10,2) NOT NULL,
    cost_price   NUMERIC(10,2),
    unit         VARCHAR(16) NOT NULL DEFAULT '件',
    launch_date  DATE
);
CREATE TABLE customers (
    customer_id    SERIAL PRIMARY KEY,
    customer_name  VARCHAR(64) NOT NULL,
    phone          VARCHAR(20),
    city           VARCHAR(32),
    customer_level VARCHAR(8) NOT NULL DEFAULT '普通',
    registered_at  DATE NOT NULL
);
CREATE TABLE orders (
    order_id     SERIAL PRIMARY KEY,
    customer_id  INT NOT NULL REFERENCES customers(customer_id),
    order_status VARCHAR(16) NOT NULL DEFAULT 'pending',
    channel      VARCHAR(16) NOT NULL DEFAULT 'online',
    created_at   TIMESTAMPTZ NOT NULL,
    paid_at      TIMESTAMPTZ,
    remark       TEXT
);
CREATE TABLE order_items (
    order_item_id SERIAL PRIMARY KEY,
    order_id      INT NOT NULL REFERENCES orders(order_id),
    product_id    INT NOT NULL REFERENCES products(product_id),
    quantity      INT NOT NULL CHECK (quantity > 0),
    unit_price    NUMERIC(10,2) NOT NULL,
    discount      NUMERIC(4,3) NOT NULL DEFAULT 0,
    CONSTRAINT chk_discount CHECK (discount >= 0 AND discount <= 1)
);
"""

#: 与 `deploy/init/03_demo_retail.sql:73-76` 逐字对应的索引集（演示库实际发布的 DDL）。
#: 只作为规模实验的「优化后」对照组使用；可 DROP 回退。
PRODUCT_INDEX_DDL = (
    "CREATE INDEX idx_orders_created ON orders(created_at);",
    "CREATE INDEX idx_orders_customer ON orders(customer_id);",
    "CREATE INDEX idx_items_order ON order_items(order_id);",
    "CREATE INDEX idx_items_product ON order_items(product_id);",
)
INDEX_SOURCE = "deploy/init/03_demo_retail.sql:73-76"

TABLE_ORDER = ("products", "customers", "orders", "order_items")
#: 小档是大档**逐行前缀**的表。`orders` 不在其中：它的 `customer_id` 取值域是本档客户数
#: （`1 + stream.below(customers)`），客户数随档位增长，所以 orders 只在「同一档位」内逐字节可复现。
#: 保留这个真实分布（20 万行订单不该只压在 50 个客户上），不用前缀性质换取整齐。
PREFIX_STABLE_TABLES = ("products", "customers", "order_items")
COPY_COLUMNS = {
    "products": ("product_id", "product_name", "category", "brand", "list_price",
                 "cost_price", "unit", "launch_date"),
    "customers": ("customer_id", "customer_name", "phone", "city", "customer_level",
                  "registered_at"),
    "orders": ("order_id", "customer_id", "order_status", "channel", "created_at",
               "paid_at", "remark"),
    "order_items": ("order_item_id", "order_id", "product_id", "quantity", "unit_price",
                    "discount"),
}

OMITTED_FIELDS = {
    "regions": "本次规模实验的固定查询集不涉及大区维度，省略整张表（连带省略 customers.region_id）",
    "products.description": "文本字段不参与固定查询集，省略以压缩生成物体积",
    "orders.remark": "保留但绝大多数为 NULL（真实演示库同列也多为空）",
    "refunds": "退款链路不在 WP8 的固定查询集内，省略整张表",
}

CATEGORIES = ("家电", "数码", "家居", "食品", "服饰")
BRANDS = ("云枢", "澄明", "启元", "澜川", "知微")
CITIES = ("上海", "北京", "杭州", "成都", "广州", "武汉")
LEVELS = ("普通", "普通", "普通", "银卡", "银卡", "金卡", "钻石")
STATUSES = ("completed", "completed", "completed", "paid", "paid", "shipped",
            "pending", "cancelled", "refunded")
CHANNELS = ("online", "online", "offline", "app")
DISCOUNTS = ("0.000", "0.000", "0.000", "0.050", "0.100", "0.150", "0.200")
BASE_DAY = date(2024, 1, 1)
ORDERS_PER_DAY = 4
MASK = (1 << 64) - 1


def _splitmix64(state: int) -> tuple[int, int]:
    """One splitmix64 step; returns (new_state, output). Pure integer arithmetic."""
    state = (state + 0x9E3779B97F4A7C15) & MASK
    z = state
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK
    return state, (z ^ (z >> 31)) & MASK


class Stream:
    """Deterministic value stream: a function of position only, never of the tier."""

    def __init__(self, seed: int = SEED):
        self.state = seed & MASK

    def next(self) -> int:
        self.state, value = _splitmix64(self.state)
        return value

    def below(self, bound: int) -> int:
        return self.next() % bound

    def pick(self, seq):
        return seq[self.below(len(seq))]

    def money(self, low_cents: int, high_cents: int) -> str:
        return f"{(low_cents + self.below(high_cents - low_cents + 1)) / 100:.2f}"


def counts_for(tier: int) -> dict:
    """Row counts per table for one tier. `order_items` is the tier itself."""
    products = 20
    customers = min(max(tier // 50, 50), 5000)
    orders = max(tier // 5, 1)
    return {"products": products, "customers": customers, "orders": orders,
            "order_items": tier}


def _tsv_row(values) -> str:
    return "\t".join("\\N" if value is None else str(value) for value in values)


def _product_rows() -> list[str]:
    rows = []
    for index in range(counts_for(1)["products"]):
        product_id = index + 1
        category = CATEGORIES[index % len(CATEGORIES)]
        brand = BRANDS[(index * 3) % len(BRANDS)]
        list_price = 19900 + index * 13750
        cost_price = None if index % 7 == 0 else f"{list_price * 62 // 100 / 100:.2f}"
        launch = date(2023, 1, 1) + timedelta(days=index * 17)
        rows.append(_tsv_row((product_id, f"{brand}{category}旗舰款{index + 1:02d}", category,
                              brand, f"{list_price / 100:.2f}", cost_price, "件",
                              launch.isoformat())))
    return rows


def _customer_rows(limit: int) -> list[str]:
    rows = []
    for index in range(limit):
        customer_id = index + 1
        stream = Stream(SEED + customer_id * 7919)
        phone = "138" + "".join(str(stream.below(10)) for _ in range(8))
        registered = date(2022, 1, 1) + timedelta(days=stream.below(900))
        rows.append(_tsv_row((customer_id, f"客户{customer_id:05d}", phone,
                              CITIES[stream.below(len(CITIES))],
                              LEVELS[stream.below(len(LEVELS))], registered.isoformat())))
    return rows


def _order_rows(limit: int, customers: int) -> list[str]:
    rows = []
    for index in range(limit):
        order_id = index + 1
        stream = Stream(SEED + order_id * 104729)
        customer_id = 1 + stream.below(customers)
        status = STATUSES[stream.below(len(STATUSES))]
        channel = CHANNELS[stream.below(len(CHANNELS))]
        created = datetime(2024, 1, 1, tzinfo=timezone(timedelta(hours=8))) + timedelta(
            days=stream.below(730), seconds=stream.below(86400))
        paid = None if status in ("pending", "cancelled") else created + timedelta(
            minutes=5 + stream.below(2880))
        remark = None if stream.below(4) else f"备注{stream.below(1000):03d}"
        rows.append(_tsv_row((order_id, customer_id, status, channel,
                              created.isoformat(), None if paid is None else paid.isoformat(),
                              remark)))
    return rows


def _order_item_rows(limit: int) -> list[str]:
    prices = []
    for line in _product_rows():
        prices.append(line.split("\t")[4])
    rows = []
    for index in range(limit):
        order_item_id = index + 1
        stream = Stream(SEED + order_item_id * 15485863)
        order_id = 1 + index // 5
        product_id = 1 + stream.below(len(prices))
        quantity = 1 + stream.below(5)
        rows.append(_tsv_row((order_item_id, order_id, product_id, quantity,
                              prices[product_id - 1], DISCOUNTS[stream.below(len(DISCOUNTS))])))
    return rows


def build_rows(tier: int) -> dict:
    """Every table's TSV lines for one tier. Row content depends only on the row number."""
    counts = counts_for(tier)
    return {"products": _product_rows()[:counts["products"]],
            "customers": _customer_rows(counts["customers"]),
            "orders": _order_rows(counts["orders"], counts["customers"]),
            "order_items": _order_item_rows(counts["order_items"])}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate(out: Path, tiers=TIERS) -> dict:
    """Write every tier under `out`; returns the manifest (also written to disk).

    每一档独立生成（不是从最大档切片）：`orders.customer_id` 的取值域是本档客户数，
    切片会让 1 千档的订单引用 1 万档才存在的 `customer_id`，直接把外键打坏。
    """
    tiers = [int(item) for item in tiers]
    files: dict[str, dict] = {}
    tier_manifest: dict[str, dict] = {}
    for tier in sorted(tiers):
        rows = build_rows(tier)
        counts = counts_for(tier)
        (out / str(tier)).mkdir(parents=True, exist_ok=True)
        table_files = {}
        for table in TABLE_ORDER:
            selected = rows[table][:counts[table]]
            text = ("\n".join(selected) + "\n") if selected else ""
            path = out / str(tier) / f"{table}.tsv"
            path.write_text(text, encoding="utf-8")
            digest = _digest(text)
            table_files[table] = {"rows": counts[table], "sha256": digest,
                                  "bytes": len(text.encode("utf-8"))}
            files[f"{tier}/{table}.tsv"] = digest
        tier_manifest[str(tier)] = {"order_items_rows": counts["order_items"],
                                    "tables": table_files}
    manifest = {
        "generated_by": GENERATOR,
        "marker": MARKER,
        "seed": SEED,
        "generator": "splitmix64 row stream; row content is a function of the row number only",
        "schema_version": SCHEMA_VERSION,
        "schema_sql_sha256": _digest(SCHEMA_SQL),
        "index_source": INDEX_SOURCE,
        "tiers": tier_manifest,
        "files": files,
        "copy_columns": {table: list(COPY_COLUMNS[table]) for table in TABLE_ORDER},
        "omitted_fields": OMITTED_FIELDS,
        "notes": [
            "同一档位逐字节可复现（--check 重新生成并比对 sha256）；products/customers/order_items "
            "同时满足「小档是大档的前缀」，orders 因 customer_id 取值域随客户数变化只在同档内可复现",
            "只写 side 目录；不写 datamind_meta、不碰 demo_retail",
            "每一行的内容只由行号与该档行数决定，不用 random 模块，不引入时钟/环境依赖",
        ],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify(out: Path, tiers=TIERS) -> list[str]:
    """Re-generate into a temp dir and diff against `out`. Returns a list of problems."""
    problems: list[str] = []
    manifest_path = out / "manifest.json"
    if not manifest_path.is_file():
        return [f"缺少 manifest：{manifest_path}"]
    stored = json.loads(manifest_path.read_text(encoding="utf-8"))
    if stored.get("marker") != MARKER:
        problems.append("manifest 缺少本生成器的 marker，拒绝把它当作本生成器产物")
    if stored.get("schema_sql_sha256") != _digest(SCHEMA_SQL):
        problems.append("建表 SQL 自本 manifest 生成后发生变化：schema_sql_sha256 不一致")
    with tempfile.TemporaryDirectory() as directory:
        fresh = generate(Path(directory), tiers)
        wanted = {f"{tier}/{table}.tsv" for tier in tiers for table in TABLE_ORDER}
        for name, digest in sorted(stored.get("files", {}).items()):
            if name not in wanted:
                continue                      # 本次只校验请求的档位，其余档位原样留在清单里
            fresh_digest = fresh["files"].get(name)
            if fresh_digest != digest:
                problems.append(f"{name}: 重新生成内容不一致 "
                                f"(manifest={digest[:16]} 重算={(fresh_digest or '缺失')[:16]})")
        for tier in sorted(stored.get("tiers", {}), key=lambda item: int(item)):
            for table, info in sorted(stored["tiers"][tier]["tables"].items()):
                path = out / tier / f"{table}.tsv"
                if not path.is_file():
                    problems.append(f"{tier}/{table}.tsv 缺失")
                    continue
                text = path.read_text(encoding="utf-8")
                lines = text.splitlines()
                if len(lines) != info["rows"]:
                    problems.append(f"{tier}/{table}.tsv 行数 {len(lines)} != manifest "
                                    f"{info['rows']}")
                if _digest(text) != info["sha256"]:
                    problems.append(f"{tier}/{table}.tsv 内容被改动（sha256 不符）")
    # 嵌套属性：小档必须是大档逐行的前缀（只对取值域不随档位变化的表成立，见 PREFIX_STABLE_TABLES）
    ordered = sorted(int(item) for item in stored.get("tiers", {}))
    for smaller, larger in zip(ordered, ordered[1:]):
        for table in PREFIX_STABLE_TABLES:
            small = (out / str(smaller) / f"{table}.tsv").read_text(encoding="utf-8").splitlines()
            big = (out / str(larger) / f"{table}.tsv").read_text(encoding="utf-8").splitlines()
            if big[:len(small)] != small:
                problems.append(f"{smaller}/{table}.tsv 不是 {larger}/{table}.tsv 的前缀"
                                "（嵌套属性被破坏）")
    return problems


def clean(out: Path, *, force: bool = False) -> None:
    """Delete only a directory this generator created, and only inside the repo `tmp/`."""
    out = out.resolve()
    root = ROOT.resolve()
    if out == root or root not in out.parents:
        raise SystemExit(f"拒绝删除仓库之外的路径：{out}")
    if out == (root / "tmp"):
        raise SystemExit("拒绝删除 tmp/ 本身")
    manifest_path = out / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"没有 {manifest_path}，拒绝删除（只能删本生成器的产物）")
    if json.loads(manifest_path.read_text(encoding="utf-8")).get("marker") != MARKER:
        raise SystemExit("manifest 的 marker 不是本生成器，拒绝删除")
    inside_tmp = (root / "tmp") in out.parents or out.parent == root / "tmp"
    if not (inside_tmp or force):
        raise SystemExit(f"{out} 不在仓库 tmp/ 下；确要删除请显式加 --force")
    shutil.rmtree(out)
    print(f"已删除 {out.relative_to(root)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--tiers", default=",".join(str(item) for item in TIERS))
    parser.add_argument("--check", action="store_true",
                        help="只读校验：重新生成到临时目录并逐表比对（不写总目录）")
    parser.add_argument("--clean", action="store_true", help="删除本生成器写出的目录")
    parser.add_argument("--force", action="store_true", help="--clean 时允许删除 tmp/ 之外的路径")
    parser.add_argument("--emit-schema", action="store_true", help="打印建表 SQL 后退出")
    parser.add_argument("--json", action="store_true", help="以 JSON 打印结果")
    args = parser.parse_args()
    tiers = tuple(int(item) for item in args.tiers.split(",") if item.strip())
    out = Path(args.out)
    out = out if out.is_absolute() else ROOT / out

    if args.emit_schema:
        print(SCHEMA_SQL.strip())
        return 0
    if args.clean:
        clean(out, force=args.force)
        return 0
    if args.check:
        problems = verify(out, tiers)
        if args.json:
            print(json.dumps({"ok": not problems, "problems": problems}, ensure_ascii=False))
        else:
            print(f"校验 {'通过' if not problems else '失败'}：{out}")
            for problem in problems:
                print(f"  - {problem}")
        return 1 if problems else 0

    manifest = generate(out, tiers)
    rows = {tier: sum(table["rows"] for table in info["tables"].values())
            for tier, info in manifest["tiers"].items()}
    if args.json:
        print(json.dumps({"out": str(out), "tiers": manifest["tiers"],
                          "total_rows": rows}, ensure_ascii=False))
    else:
        print(f"已生成 {out}")
        for tier in sorted(manifest["tiers"], key=lambda item: int(item)):
            tables = manifest["tiers"][tier]["tables"]
            print(f"  {tier:>7} 档: " + " ".join(
                f"{table}={tables[table]['rows']}" for table in TABLE_ORDER) +
                f" 合计={rows[tier]}")
        print("  manifest: " + str((out / "manifest.json").relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
