"""Repository scanner for AST harvesting.

Scans cloned repositories for harvestable functions, filtering
out test files, vendor directories, and generated files.
"""

import logging
import subprocess
import tempfile
from pathlib import Path

from ingest.ast_harvest import ASTHarvester, HarvestedFunction

logger = logging.getLogger(__name__)

# Directories to skip when scanning
SKIP_DIRS = {
    "test", "tests", "testing",
    "vendor", "third_party", "thirdparty", "third-party",
    "node_modules", ".git", "__pycache__",
    "build", "dist", "cmake-build-debug", "cmake-build-release",
    "generated", "gen", "auto",
    "examples", "example", "docs", "doc",
    "benchmark", "benchmarks", "bench",
    ".github", ".vscode", ".idea",
}

# File patterns to skip
SKIP_FILE_PATTERNS = {
    "test_", "_test.", "tests.", "testing.",
    "conftest.", "setup.", "config.",
    "__init__.", "__main__.",
    ".generated.", ".auto.",
}

# License file names to check
LICENSE_FILES = [
    "LICENSE", "LICENSE.md", "LICENSE.txt",
    "COPYING", "COPYING.md", "COPYING.txt",
    "LICENSE-MIT", "LICENSE-APACHE",
    "license", "license.md", "license.txt",
]

# Language to file extension mapping
LANG_EXTENSIONS = {
    "c": {".c"},
    "python": {".py"},
    "go": {".go"},
    "rust": {".rs"},
}


class RepoScanner:
    """Scans repositories for harvestable functions."""

    def __init__(self) -> None:
        self._harvester = ASTHarvester()

    def scan_repo(
        self, repo_path: Path, language: str
    ) -> list[HarvestedFunction]:
        """Scan a cloned repo for harvestable functions.

        Args:
            repo_path: Root directory of the cloned repository
            language: Language to scan for ("c", "python", "go", "rust")

        Returns:
            List of harvested functions with license info populated.
        """
        if language not in LANG_EXTENSIONS:
            logger.warning("Unsupported language: %s", language)
            return []

        extensions = LANG_EXTENSIONS[language]
        license_text = self._detect_license(repo_path)

        results = []
        for source_file in self._iter_source_files(repo_path, extensions):
            funcs = self._harvest_file(source_file, language)
            for func in funcs:
                func.license = license_text
            results.extend(funcs)

        logger.info(
            "Scanned %s: found %d functions (%s)",
            repo_path.name, len(results), language,
        )
        return results

    def clone_and_scan(
        self, repo_url: str, language: str
    ) -> list[HarvestedFunction]:
        """Clone a repo (shallow) and scan for harvestable functions.

        Args:
            repo_url: Git repository URL
            language: Language to scan for

        Returns:
            List of harvested functions.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "repo"
            logger.info("Cloning %s to %s", repo_url, dest)
            subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(dest)],
                check=True,
                capture_output=True,
            )
            return self.scan_repo(dest, language)

    def _iter_source_files(
        self, repo_path: Path, extensions: set[str]
    ) -> list[Path]:
        """Iterate over source files, skipping filtered directories and files."""
        source_files = []
        for path in repo_path.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in extensions:
                continue
            if self._should_skip(path, repo_path):
                continue
            source_files.append(path)
        return sorted(source_files)

    def _should_skip(self, path: Path, repo_root: Path) -> bool:
        """Check if a file should be skipped based on path patterns."""
        # Check directory components
        rel = path.relative_to(repo_root)
        for part in rel.parts[:-1]:  # directories only
            if part.lower() in SKIP_DIRS:
                return True

        # Check file name patterns
        filename = path.name.lower()
        for pattern in SKIP_FILE_PATTERNS:
            if pattern in filename:
                return True

        return False

    def _harvest_file(
        self, source_file: Path, language: str
    ) -> list[HarvestedFunction]:
        """Harvest functions from a single source file."""
        if language == "c":
            return self._harvester.harvest_c_functions(source_file)
        elif language == "python":
            return self._harvester.harvest_python_functions(source_file)
        else:
            return []

    def _detect_license(self, repo_path: Path) -> str:
        """Detect the license type from the repo root.

        Returns a short license identifier string (e.g. "MIT", "Apache-2.0")
        or "unknown" if not detected.
        """
        for name in LICENSE_FILES:
            license_path = repo_path / name
            if license_path.is_file():
                try:
                    text = license_path.read_text(
                        encoding="utf-8", errors="replace"
                    )[:2000]  # first 2000 chars
                    return self._classify_license(text)
                except (OSError, IOError):
                    continue
        return "unknown"

    def _classify_license(self, text: str) -> str:
        """Classify license text into a short identifier."""
        text_lower = text.lower()

        if "mit license" in text_lower or "permission is hereby granted" in text_lower:
            return "MIT"
        if "apache license" in text_lower and "version 2" in text_lower:
            return "Apache-2.0"
        if "bsd" in text_lower:
            if "3-clause" in text_lower or "three clause" in text_lower:
                return "BSD-3-Clause"
            if "2-clause" in text_lower or "two clause" in text_lower:
                return "BSD-2-Clause"
            return "BSD"
        if "mozilla public license" in text_lower:
            return "MPL-2.0"
        if "gnu general public license" in text_lower:
            return "GPL"
        if "isc license" in text_lower:
            return "ISC"
        if "unlicense" in text_lower or "public domain" in text_lower:
            return "Unlicense"

        return "unknown"
