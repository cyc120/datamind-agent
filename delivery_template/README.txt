========================================================================
 DataMind Agent —— 本地演示便携包（免 Docker / 免 Python / 免装数据库）
========================================================================

【怎么用】三步
  1. 把整个压缩包解压到一个文件夹（路径可以有中文，但请避免解压到
     C:\Program Files 这类需要管理员权限的位置）。
  2. 双击 run.bat
  3. 等 10~40 秒，浏览器会自动打开 http://127.0.0.1:8000/

  第一次启动（或换了一台机器）会慢一些，通常 1~2 分钟内一定能起来。
  期间那个黑色窗口会打印启动进度，请让它保持打开。

【包里是什么】
  runtime\pgsql    便携版 PostgreSQL 16.4（数据目录 data\pgdata，已预置好演示数据）
  runtime\qdrant   便携版 Qdrant 1.12.4（向量库，data\qdrant_storage 已预置向量）
  runtime\python   嵌入式 Python 3.14.5 + runtime\pylibs（全部第三方依赖）
  app\             项目源码（datamind / db / ingestion / web / docs …）
  data\            预置数据库、向量库、日志、模型配置
  tools\           启动/停止用的辅助脚本

  全部运行时都在这个文件夹里：不需要 Docker，不需要装 Python，
  不需要装 PostgreSQL，也不会往系统目录、注册表或系统服务里写任何东西。

【端口】刻意避开了常用端口，不会和你机器上已有的数据库打架
  Web 服务        8000
  PostgreSQL      55432   （不是 5432）
  Qdrant          56333   （不是 6333）

【怎么停】
  * 在 run.bat 的窗口里按任意键 → 服务全部停止；
  * 或者双击 stop.bat；
  * 直接关掉窗口也可以：后台看门狗会把 PostgreSQL / Qdrant 一起收掉
    （万一没收干净，双击 stop.bat 一次即可）。

【要能问答，得有大模型 API Key】
  本包出厂**不带任何密钥**。两种填法，任选一种：

  甲）在页面里填（推荐，最省事）
      打开页面左侧「模型接入」→ 填接口地址 / 模型名 / API Key → 保存。
      密钥存在预置的本地数据库里（readonly 角色读不到），不落任何文件。

  乙）用文件填（先填后用，适合批量部署）
      把 data\model_config.env.example 复制成 data\model_config.env，
      按里面的说明填好，再启动 run.bat。
      注意：填了真 Key 之后，这个文件夹就带着密钥了，别再转发给别人。

  常见模型服务（OpenAI 兼容协议）：
      DeepSeek:  https://api.deepseek.com/v1      模型 deepseek-chat
      其它兼容服务同理，填它们的 /v1 地址即可。

【启动时如果弹杀毒 / 防火墙窗口】
  选「允许」。程序只监听 127.0.0.1（本机回环），不对局域网开放。
  若杀毒软件把 runtime\python\python.exe 或 runtime\qdrant\qdrant.exe
  隔离了，恢复它并加白名单，然后重新双击 run.bat。

【出问题看哪里】
  data\logs\postgres.log      数据库日志
  data\logs\qdrant.err.log    向量库日志
  data\logs\api_console.log   Web 服务日志（run.bat 窗口里也会实时打印）
  data\logs\upload_worker.log 文件上传解析 worker

【不要提交给 git】
  这个目录是打包产物，仓库根目录的 .gitignore 已经把 delivery/ 和
  delivery_cache/ 排除掉了。

========================================================================
