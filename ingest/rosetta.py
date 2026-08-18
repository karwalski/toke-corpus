"""Rosetta Code ingestion pipeline.

Clones the Rosetta Code structured data mirror, extracts Python and C
solutions, transpiles them to toke via PyToTokeTranspiler / CToTokeTranspiler,
validates with tkc, and produces a JSONL corpus file with provenance metadata.

License: GFDL 1.2 (Rosetta Code content).
"""

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from transpile.c_to_toke import CToTokeTranspiler
from transpile.c_to_toke import TranspileError as CTranspileError
from transpile.py_to_toke import PyToTokeTranspiler
from transpile.py_to_toke import TranspileError as PyTranspileError

logger = logging.getLogger(__name__)

TKC_BINARY = Path("/Users/matthew.watt/tk/toke/tkc")

ROSETTA_REPO_URL = "https://github.com/acmeism/RosettaCodeData.git"

# Language directory names in the RosettaCodeData repo
LANG_DIR_MAP = {
    "python": "Python",
    "c": "C",
}

# File extensions per language
LANG_EXT_MAP = {
    "python": ".py",
    "c": ".c",
}


@dataclass
class RosettaTask:
    """Metadata about a single Rosetta Code task."""

    name: str
    slug: str
    languages: dict[str, Path] = field(default_factory=dict)


@dataclass
class IngestReport:
    """Summary statistics from a full Rosetta Code ingestion run."""

    total_tasks: int = 0
    transpile_success: int = 0
    transpile_fail: int = 0
    tkc_pass: int = 0
    tkc_fail: int = 0
    auto_repair_success: int = 0
    entries_written: int = 0


class RosettaIngestor:
    """Pipeline for ingesting Rosetta Code tasks into the toke corpus."""

    def __init__(self, tkc_path: Path | None = None) -> None:
        self.tkc_path = tkc_path or TKC_BINARY
        self.py_transpiler = PyToTokeTranspiler()
        self.c_transpiler = CToTokeTranspiler()

    def clone_repo(self, dest: Path) -> None:
        """Shallow clone the Rosetta Code structured data repo.

        Args:
            dest: Directory to clone into
        """
        logger.info("Cloning %s to %s", ROSETTA_REPO_URL, dest)
        subprocess.run(
            ["git", "clone", "--depth", "1", ROSETTA_REPO_URL, str(dest)],
            check=True,
            capture_output=True,
        )

    def list_tasks(self, repo_dir: Path) -> list[RosettaTask]:
        """Find all tasks that have Python and/or C implementations.

        The RosettaCodeData repo structure is:
            Task/<TaskName>/<Language>/<solution-files>

        Args:
            repo_dir: Root of the cloned RosettaCodeData repository

        Returns:
            List of RosettaTask for tasks with at least one supported language.
        """
        task_root = repo_dir / "Task"
        if not task_root.is_dir():
            logger.warning("No Task directory found at %s", task_root)
            return []

        tasks: list[RosettaTask] = []
        for task_dir in sorted(task_root.iterdir()):
            if not task_dir.is_dir():
                continue

            task_name = task_dir.name
            slug = self._make_slug(task_name)
            languages: dict[str, Path] = {}

            for lang_key, lang_dirname in LANG_DIR_MAP.items():
                lang_dir = task_dir / lang_dirname
                if not lang_dir.is_dir():
                    continue
                ext = LANG_EXT_MAP[lang_key]
                # Find the first solution file with the right extension
                solution_files = sorted(
                    f for f in lang_dir.iterdir()
                    if f.is_file() and f.suffix == ext
                )
                if solution_files:
                    languages[lang_key] = solution_files[0]

            if languages:
                tasks.append(
                    RosettaTask(
                        name=task_name,
                        slug=slug,
                        languages=languages,
                    )
                )

        logger.info(
            "Found %d tasks with at least one supported language", len(tasks)
        )
        return tasks

    def extract_solution(self, task: RosettaTask, language: str) -> str:
        """Read the solution source file for a given language.

        Args:
            task: Task metadata with language->path mapping
            language: Language key, e.g. "python" or "c"

        Returns:
            Source code as a string

        Raises:
            KeyError: If the task does not have a solution in the given language
        """
        path = task.languages[language]
        return path.read_text(encoding="utf-8", errors="replace")

    def transpile_solution(
        self, source: str, language: str, task_name: str
    ) -> str:
        """Transpile a solution to toke.

        Args:
            source: Source code in the original language
            language: "python" or "c"
            task_name: Used to derive the toke module name

        Returns:
            toke source code

        Raises:
            PyTranspileError or CTranspileError: If transpilation fails
        """
        module_name = self._make_slug(task_name)
        if language == "python":
            return self.py_transpiler.transpile(source, module_name)
        elif language == "c":
            return self.c_transpiler.transpile(source, module_name)
        else:
            raise ValueError(f"Unsupported language: {language}")

    def validate_toke(self, tk_source: str) -> tuple[bool, str]:
        """Validate toke source using tkc --check.

        Args:
            tk_source: toke source code to validate

        Returns:
            Tuple of (passed, diagnostic_output). passed is True if
            tkc exits 0; diagnostic_output contains stderr on failure.
        """
        with tempfile.NamedTemporaryFile(
            suffix=".tk", mode="w", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(tk_source)
            tmp_path = Path(tmp.name)

        try:
            result = subprocess.run(
                [str(self.tkc_path), "--check", str(tmp_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True, ""
            return False, result.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return False, str(exc)
        finally:
            tmp_path.unlink(missing_ok=True)

    def auto_repair(self, tk_source: str, diag: str) -> str | None:
        """Attempt one auto-repair pass on failing toke source.

        Handles:
        - Missing semicolon: inserts `;` at the reported position
        - Immutable reassignment: converts `let x=val;` to `let x=mut.val;`

        Args:
            tk_source: toke source that failed validation
            diag: Diagnostic output from tkc

        Returns:
            Repaired source, or None if the error is not auto-repairable
        """
        # Missing semicolon — look for line/column in diagnostic
        semi_match = re.search(
            r"[Ee]xpected\s+['\"]?;['\"]?.*?(?:line|ln)\s*[:=]?\s*(\d+).*?(?:col|column)\s*[:=]?\s*(\d+)",
            diag,
        )
        if not semi_match:
            # Try alternate format: "line N, col M"
            semi_match = re.search(
                r"(?:line|ln)\s*[:=]?\s*(\d+).*?(?:col|column)\s*[:=]?\s*(\d+).*?[Ee]xpected\s+['\"]?;['\"]?",
                diag,
            )

        if semi_match:
            line_no = int(semi_match.group(1))
            col_no = int(semi_match.group(2))
            lines = tk_source.split("\n")
            if 1 <= line_no <= len(lines):
                line = lines[line_no - 1]
                # Insert semicolon at the reported column
                insert_pos = min(col_no - 1, len(line))
                lines[line_no - 1] = line[:insert_pos] + ";" + line[insert_pos:]
                return "\n".join(lines)

        # Immutable reassignment — convert let x=val to let x=mut.val
        immut_match = re.search(
            r"[Ii]mmutable.*?reassign.*?['\"](\w+)['\"]",
            diag,
        )
        if not immut_match:
            immut_match = re.search(
                r"[Cc]annot\s+assign.*?immutable.*?['\"](\w+)['\"]",
                diag,
            )

        if immut_match:
            var_name = immut_match.group(1)
            # Find `let varname=` (without mut.) and add mut.
            pattern = re.compile(
                rf"(let\s+{re.escape(var_name)}\s*=\s*)(?!mut\.)"
            )
            repaired = pattern.sub(rf"\g<1>mut.", tk_source, count=1)
            if repaired != tk_source:
                return repaired

        return None

    def ingest(
        self, output_path: Path, languages: list[str]
    ) -> IngestReport:
        """Run the full ingestion pipeline.

        Clone -> list tasks -> extract -> transpile -> validate ->
        auto-repair if needed -> write JSONL.

        Args:
            output_path: Path for the output JSONL file
            languages: Ordered list of languages to try, e.g. ["python", "c"]

        Returns:
            IngestReport with pipeline statistics
        """
        report = IngestReport()

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir) / "RosettaCodeData"
            self.clone_repo(repo_dir)

            tasks = self.list_tasks(repo_dir)
            report.total_tasks = len(tasks)

            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as out:
                for task in tasks:
                    entry = self._process_task(task, languages, report)
                    if entry is not None:
                        out.write(json.dumps(entry, ensure_ascii=False) + "\n")
                        report.entries_written += 1

        logger.info(
            "Ingestion complete: %d/%d transpiled, %d/%d tkc pass, "
            "%d auto-repaired, %d written",
            report.transpile_success,
            report.total_tasks,
            report.tkc_pass,
            report.transpile_success,
            report.auto_repair_success,
            report.entries_written,
        )
        return report

    def _process_task(
        self,
        task: RosettaTask,
        languages: list[str],
        report: IngestReport,
    ) -> dict | None:
        """Process a single task through transpile + validate.

        Tries each language in order; returns the first successful result
        (deduplication: earlier languages in the list are preferred).

        Returns a corpus entry dict or None if no language produced valid toke.
        """
        for lang in languages:
            if lang not in task.languages:
                continue

            source = self.extract_solution(task, lang)

            # Transpile
            try:
                tk_source = self.transpile_solution(source, lang, task.name)
            except (PyTranspileError, CTranspileError, ValueError) as exc:
                logger.debug(
                    "Transpile failed for %s/%s: %s", task.name, lang, exc
                )
                report.transpile_fail += 1
                continue

            report.transpile_success += 1

            # Validate
            passed, diag = self.validate_toke(tk_source)

            if not passed:
                # Try auto-repair
                repaired = self.auto_repair(tk_source, diag)
                if repaired is not None:
                    passed_after, _ = self.validate_toke(repaired)
                    if passed_after:
                        tk_source = repaired
                        passed = True
                        report.auto_repair_success += 1

            if passed:
                report.tkc_pass += 1
            else:
                report.tkc_fail += 1
                continue

            # Build corpus entry
            tk_tokens = len(tk_source.split())
            entry = {
                "id": f"rosetta-{lang}-{task.slug}-001",
                "tk_source": tk_source,
                "tk_tokens": tk_tokens,
                "generation_method": "transpilation",
                "source": {
                    "origin": "rosettacode",
                    "task": task.name,
                    "language": lang,
                    "license": "GFDL-1.2",
                    "retrieval_date": str(date.today()),
                },
                "validation": {"tkc_check": "pass"},
            }
            return entry

        return None

    @staticmethod
    def _make_slug(name: str) -> str:
        """Convert a task name to a toke-compatible slug.

        Strips non-alphanumeric characters, lowercases, removes underscores.
        """
        slug = re.sub(r"[^a-zA-Z0-9]", "", name).lower()
        return slug or "unnamed"
