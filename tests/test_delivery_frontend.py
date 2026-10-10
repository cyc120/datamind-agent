"""The delivery plan must include the current UI without bundling local build caches."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import build_delivery_zip


class DeliveryFrontendTests(unittest.TestCase):
    def test_vue_sources_and_deployed_assets_are_included_without_caches(self):
        required = (
            "frontend/package.json",
            "frontend/package-lock.json",
            "frontend/index.html",
            "frontend/vite.config.ts",
            "frontend/src/main.ts",
            "frontend/src/views/MainPage.vue",
            "web/vue/index.html",
            "web/vue/assets/index.js",
        )
        caches = (
            "frontend/node_modules/vue/index.js",
            "frontend/dist/index.html",
            "frontend/dist-ssr/entry.js",
            "frontend/.vite/cache.js",
            "frontend/node_modules/.vite/cache.js",
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (*required, *caches):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            # Include cache paths in the tracked list too: exclusion must hold whether
            # a file comes from git ls-files or the filesystem walk.
            with patch.object(build_delivery_zip, "ROOT", root), \
                    patch.object(build_delivery_zip, "git_files", return_value=list(required + caches)):
                plan = build_delivery_zip.collect()

        included = {item["path"] for item in plan["files"]}
        self.assertTrue(set(required).issubset(included), set(required) - included)
        self.assertFalse(set(caches) & included, set(caches) & included)
        source = next(section for section in plan["sections"] if section["id"] == "source_code")
        self.assertIn("frontend/", source["paths"])
        self.assertFalse(plan["zip_written"])


if __name__ == "__main__":
    unittest.main()
