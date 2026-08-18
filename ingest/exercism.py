"""Exercism ingestion pipeline.

Clones the Exercism Python track, extracts exemplar solutions,
transpiles them to toke via PyToTokeTranspiler, validates with tkc,
and produces a JSONL corpus file with provenance metadata.
"""

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from transpile.py_to_toke import PyToTokeTranspiler, TranspileError

logger = logging.getLogger(__name__)

TKC_BINARY = Path("/Users/matthew.watt/tk/toke/tkc")


@dataclass
class ExerciseInfo:
    """Metadata about a single Exercism practice exercise."""

    name: str
    exemplar_path: Path
    test_path: Path


@dataclass
class IngestReport:
    """Summary statistics from a full track ingestion run."""

    total_exercises: int = 0
    transpile_success: int = 0
    transpile_fail: int = 0
    tkc_pass: int = 0
    tkc_fail: int = 0
    auto_repair_success: int = 0
    entries_written: int = 0


class ExercismIngestor:
    """Pipeline for ingesting Exercism exercises into the toke corpus."""

    def __init__(self, tkc_path: Path | None = None) -> None:
        self.tkc_path = tkc_path or TKC_BINARY
        self.transpiler = PyToTokeTranspiler()

    def clone_track(self, track: str, dest: Path) -> None:
        """Clone an Exercism language track repo (shallow clone).

        Args:
            track: Language track name, e.g. "python"
            dest: Directory to clone into
        """
        url = f"https://github.com/exercism/{track}.git"
        logger.info("Cloning %s to %s", url, dest)
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            check=True,
            capture_output=True,
        )

    def list_exercises(self, track_dir: Path) -> list[ExerciseInfo]:
        """Find all practice exercises with exemplar solutions.

        Args:
            track_dir: Root of the cloned track repository

        Returns:
            List of ExerciseInfo for exercises that have both an
            exemplar solution and a test file.
        """
        practice_dir = track_dir / "exercises" / "practice"
        if not practice_dir.is_dir():
            logger.warning("No practice directory found at %s", practice_dir)
            return []

        exercises: list[ExerciseInfo] = []
        for exercise_dir in sorted(practice_dir.iterdir()):
            if not exercise_dir.is_dir():
                continue

            name = exercise_dir.name
            exemplar = exercise_dir / ".meta" / "exemplar.py"
            # Test file uses underscores instead of hyphens
            test_name = name.replace("-", "_") + "_test.py"
            test_file = exercise_dir / test_name

            if exemplar.is_file() and test_file.is_file():
                exercises.append(
                    ExerciseInfo(
                        name=name,
                        exemplar_path=exemplar,
                        test_path=test_file,
                    )
                )
            else:
                logger.debug(
                    "Skipping %s: exemplar=%s test=%s",
                    name,
                    exemplar.exists(),
                    test_file.exists(),
                )

        logger.info("Found %d exercises with exemplar solutions", len(exercises))
        return exercises

    def extract_solution(self, exercise: ExerciseInfo) -> str:
        """Read the exemplar Python solution file.

        Args:
            exercise: Exercise metadata with path to exemplar

        Returns:
            Python source code as a string
        """
        return exercise.exemplar_path.read_text(encoding="utf-8")

    def transpile_solution(self, python_source: str, exercise_name: str) -> str:
        """Transpile a Python solution to toke.

        Args:
            python_source: Python source code
            exercise_name: Used as the toke module name (hyphens removed)

        Returns:
            toke source code

        Raises:
            TranspileError: If the source cannot be transpiled
        """
        module_name = exercise_name.replace("-", "")
        return self.transpiler.transpile(python_source, module_name)

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

    def ingest_track(self, track: str, output_path: Path) -> IngestReport:
        """Run the full ingestion pipeline for an Exercism track.

        Clone -> list exercises -> extract -> transpile -> validate ->
        auto-repair if needed -> write JSONL.

        Args:
            track: Language track, e.g. "python"
            output_path: Path for the output JSONL file

        Returns:
            IngestReport with pipeline statistics
        """
        report = IngestReport()

        with tempfile.TemporaryDirectory() as tmpdir:
            track_dir = Path(tmpdir) / track
            self.clone_track(track, track_dir)

            exercises = self.list_exercises(track_dir)
            report.total_exercises = len(exercises)

            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as out:
                for exercise in exercises:
                    entry = self._process_exercise(exercise, track, report)
                    if entry is not None:
                        out.write(json.dumps(entry, ensure_ascii=False) + "\n")
                        report.entries_written += 1

        logger.info(
            "Ingestion complete: %d/%d transpiled, %d/%d tkc pass, "
            "%d auto-repaired, %d written",
            report.transpile_success,
            report.total_exercises,
            report.tkc_pass,
            report.transpile_success,
            report.auto_repair_success,
            report.entries_written,
        )
        return report

    def _process_exercise(
        self,
        exercise: ExerciseInfo,
        track: str,
        report: IngestReport,
    ) -> dict | None:
        """Process a single exercise through transpile + validate.

        Returns a corpus entry dict or None if the exercise could not
        be processed.
        """
        python_source = self.extract_solution(exercise)

        # Transpile
        try:
            tk_source = self.transpile_solution(python_source, exercise.name)
        except TranspileError as exc:
            logger.debug("Transpile failed for %s: %s", exercise.name, exc)
            report.transpile_fail += 1
            return None

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
            return None

        # Build corpus entry
        tk_tokens = len(tk_source.split())
        entry = {
            "id": f"exercism-{track}-{exercise.name}-001",
            "tk_source": tk_source,
            "tk_tokens": tk_tokens,
            "generation_method": "transpilation",
            "source": {
                "origin": "exercism",
                "track": track,
                "exercise": exercise.name,
                "license": "CC-BY-SA-3.0",
                "retrieval_date": str(date.today()),
            },
            "validation": {"tkc_check": "pass"},
        }
        return entry


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(
        description="Ingest Exercism exercises into toke corpus"
    )
    parser.add_argument(
        "--track",
        default="python",
        help="Exercism track to ingest (default: python)",
    )
    parser.add_argument(
        "--output",
        default="data/corpus_transpiled_exercism.jsonl",
        help="Output JSONL path",
    )
    args = parser.parse_args()

    ingestor = ExercismIngestor()
    result = ingestor.ingest_track(args.track, Path(args.output))

    print(f"Total exercises:      {result.total_exercises}")
    print(f"Transpile success:    {result.transpile_success}")
    print(f"Transpile fail:       {result.transpile_fail}")
    print(f"tkc pass:             {result.tkc_pass}")
    print(f"tkc fail:             {result.tkc_fail}")
    print(f"Auto-repair success:  {result.auto_repair_success}")
    print(f"Entries written:      {result.entries_written}")
