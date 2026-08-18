"""Snippet collection ingestion pipeline (Story 9.4.3).

Clones snippet repos (30-seconds-of-python, go-by-example, etc.),
extracts small self-contained functions, transpiles Python snippets
to toke via PyToTokeTranspiler, validates with tkc, and produces
a JSONL corpus file with provenance metadata.
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
class SnippetRepo:
    """Describes a snippet source repository."""

    name: str
    url: str
    license: str
    language: str
    snippet_glob: str


@dataclass
class Snippet:
    """A single extracted code snippet."""

    name: str
    source: str
    language: str
    repo_name: str
    license: str


@dataclass
class IngestReport:
    """Summary statistics from a full snippet ingestion run."""

    total_snippets: int = 0
    transpile_success: int = 0
    transpile_fail: int = 0
    tkc_pass: int = 0
    tkc_fail: int = 0
    auto_repair_success: int = 0
    entries_written: int = 0
    skipped_go: int = 0


DEFAULT_REPOS: list[SnippetRepo] = [
    SnippetRepo(
        name="30-seconds-of-python",
        url="https://github.com/30-seconds/30-seconds-of-python.git",
        license="CC-BY-4.0",
        language="python",
        snippet_glob="snippets/**/*.md",
    ),
    SnippetRepo(
        name="go-by-example",
        url="https://github.com/mmcgrana/gobyexample.git",
        license="CC-BY-3.0",
        language="go",
        snippet_glob="examples/**/*.go",
    ),
]


class SnippetIngestor:
    """Pipeline for ingesting snippet collections into the toke corpus."""

    def __init__(self, tkc_path: Path | None = None) -> None:
        self.tkc_path = tkc_path or TKC_BINARY
        self.transpiler = PyToTokeTranspiler()

    def clone_repo(self, repo: SnippetRepo, dest: Path) -> None:
        """Shallow clone a snippet repository.

        Args:
            repo: Snippet repo descriptor
            dest: Directory to clone into
        """
        logger.info("Cloning %s to %s", repo.url, dest)
        subprocess.run(
            ["git", "clone", "--depth", "1", repo.url, str(dest)],
            check=True,
            capture_output=True,
        )

    def extract_snippets_python_md(
        self, repo_dir: Path, glob_pattern: str
    ) -> list[Snippet]:
        """Extract Python code blocks from markdown files.

        Handles the 30-seconds-of-python format where each .md file
        contains fenced Python code blocks with small utility functions.

        Args:
            repo_dir: Root of the cloned repo
            glob_pattern: Glob for finding markdown files

        Returns:
            List of extracted Snippet objects
        """
        snippets: list[Snippet] = []
        md_files = sorted(repo_dir.glob(glob_pattern))

        for md_path in md_files:
            try:
                content = md_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                logger.debug("Skipping %s: %s", md_path, exc)
                continue

            # Extract fenced Python code blocks
            code_blocks = re.findall(
                r"```(?:python|py)\s*\n(.*?)```",
                content,
                re.DOTALL,
            )

            snippet_name = md_path.stem
            for i, block in enumerate(code_blocks):
                block = block.strip()
                if not block:
                    continue
                # Only keep blocks that contain function definitions
                if "def " not in block:
                    continue

                name = snippet_name if i == 0 else f"{snippet_name}{i}"
                snippets.append(
                    Snippet(
                        name=name,
                        source=block,
                        language="python",
                        repo_name="30-seconds-of-python",
                        license="CC-BY-4.0",
                    )
                )

        logger.info(
            "Extracted %d Python snippets from %d markdown files",
            len(snippets),
            len(md_files),
        )
        return snippets

    def extract_snippets_go(self, repo_dir: Path) -> list[Snippet]:
        """Extract Go functions from .go files in examples/.

        Args:
            repo_dir: Root of the cloned go-by-example repo

        Returns:
            List of extracted Snippet objects
        """
        snippets: list[Snippet] = []
        examples_dir = repo_dir / "examples"
        if not examples_dir.is_dir():
            logger.warning("No examples/ directory found at %s", examples_dir)
            return []

        go_files = sorted(examples_dir.rglob("*.go"))

        for go_path in go_files:
            try:
                content = go_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                logger.debug("Skipping %s: %s", go_path, exc)
                continue

            # Extract func declarations (simple heuristic)
            func_matches = re.findall(
                r"^(func\s+\w+\s*\(.*?\).*?\{.*?^})",
                content,
                re.DOTALL | re.MULTILINE,
            )

            if not func_matches:
                continue

            # Use directory name as snippet group name
            snippet_name = go_path.parent.name
            if snippet_name == "examples":
                snippet_name = go_path.stem

            for i, func_source in enumerate(func_matches):
                # Extract function name
                name_match = re.match(r"func\s+(\w+)", func_source)
                if not name_match:
                    continue
                func_name = name_match.group(1)
                # Skip main functions
                if func_name == "main":
                    continue

                name = f"{snippet_name}-{func_name}" if i > 0 else snippet_name

                snippets.append(
                    Snippet(
                        name=name,
                        source=func_source,
                        language="go",
                        repo_name="go-by-example",
                        license="CC-BY-3.0",
                    )
                )

        logger.info("Extracted %d Go snippets", len(snippets))
        return snippets

    def transpile_snippet(self, snippet: Snippet) -> str | None:
        """Transpile a snippet to toke.

        Python snippets are transpiled via PyToTokeTranspiler.
        Go snippets are saved for future Go transpiler (returns None).

        Args:
            snippet: The snippet to transpile

        Returns:
            toke source code, or None if not transpilable
        """
        if snippet.language == "go":
            # Go transpiler not yet available
            return None

        if snippet.language != "python":
            return None

        module_name = re.sub(r"[^a-z0-9]", "", snippet.name.lower())
        if not module_name:
            module_name = "snippet"

        try:
            return self.transpiler.transpile(snippet.source, module_name)
        except TranspileError as exc:
            logger.debug(
                "Transpile failed for %s: %s", snippet.name, exc
            )
            return None

    def validate_toke(self, tk_source: str) -> tuple[bool, str]:
        """Validate toke source using tkc --check.

        Args:
            tk_source: toke source code to validate

        Returns:
            Tuple of (passed, diagnostic_output).
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
        # Missing semicolon
        semi_match = re.search(
            r"[Ee]xpected\s+['\"]?;['\"]?.*?(?:line|ln)\s*[:=]?\s*(\d+).*?(?:col|column)\s*[:=]?\s*(\d+)",
            diag,
        )
        if not semi_match:
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
                insert_pos = min(col_no - 1, len(line))
                lines[line_no - 1] = line[:insert_pos] + ";" + line[insert_pos:]
                return "\n".join(lines)

        # Immutable reassignment
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
            pattern = re.compile(
                rf"(let\s+{re.escape(var_name)}\s*=\s*)(?!mut\.)"
            )
            repaired = pattern.sub(rf"\g<1>mut.", tk_source, count=1)
            if repaired != tk_source:
                return repaired

        return None

    def ingest_all(self, output_path: Path) -> IngestReport:
        """Run the full ingestion pipeline across all snippet repos.

        Clone -> extract -> transpile -> validate -> auto-repair -> write JSONL.

        Args:
            output_path: Path for the output JSONL file

        Returns:
            IngestReport with pipeline statistics
        """
        report = IngestReport()
        all_snippets: list[Snippet] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            for repo in DEFAULT_REPOS:
                repo_dir = Path(tmpdir) / repo.name
                self.clone_repo(repo, repo_dir)

                if repo.language == "python":
                    snippets = self.extract_snippets_python_md(
                        repo_dir, repo.snippet_glob
                    )
                elif repo.language == "go":
                    snippets = self.extract_snippets_go(repo_dir)
                else:
                    logger.warning("Unsupported language: %s", repo.language)
                    continue

                all_snippets.extend(snippets)

        report.total_snippets = len(all_snippets)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as out:
            for snippet in all_snippets:
                entry = self._process_snippet(snippet, report)
                if entry is not None:
                    out.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    report.entries_written += 1

        logger.info(
            "Ingestion complete: %d total, %d transpiled, %d tkc pass, "
            "%d auto-repaired, %d go skipped, %d written",
            report.total_snippets,
            report.transpile_success,
            report.tkc_pass,
            report.auto_repair_success,
            report.skipped_go,
            report.entries_written,
        )
        return report

    def _process_snippet(
        self, snippet: Snippet, report: IngestReport
    ) -> dict | None:
        """Process a single snippet through transpile + validate.

        Returns a corpus entry dict or None if not processable.
        """
        # Go snippets saved for future transpiler
        if snippet.language == "go":
            report.skipped_go += 1
            return None

        tk_source = self.transpile_snippet(snippet)
        if tk_source is None:
            report.transpile_fail += 1
            return None

        report.transpile_success += 1

        # Validate
        passed, diag = self.validate_toke(tk_source)

        if not passed:
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
        sanitized_name = re.sub(r"[^a-z0-9]", "", snippet.name.lower())
        entry = {
            "id": f"snippet-{snippet.repo_name}-{sanitized_name}-001",
            "tk_source": tk_source,
            "tk_tokens": tk_tokens,
            "generation_method": "transpilation",
            "source": {
                "origin": snippet.repo_name,
                "snippet": snippet.name,
                "language": snippet.language,
                "license": snippet.license,
                "retrieval_date": str(date.today()),
            },
            "validation": {"tkc_check": "pass"},
        }
        return entry


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(
        description="Ingest snippet collections into toke corpus"
    )
    parser.add_argument(
        "--output",
        default="data/corpus_transpiled_snippets.jsonl",
        help="Output JSONL path",
    )
    args = parser.parse_args()

    ingestor = SnippetIngestor()
    result = ingestor.ingest_all(Path(args.output))

    print(f"Total snippets:       {result.total_snippets}")
    print(f"Transpile success:    {result.transpile_success}")
    print(f"Transpile fail:       {result.transpile_fail}")
    print(f"tkc pass:             {result.tkc_pass}")
    print(f"tkc fail:             {result.tkc_fail}")
    print(f"Auto-repair success:  {result.auto_repair_success}")
    print(f"Skipped (Go):         {result.skipped_go}")
    print(f"Entries written:      {result.entries_written}")
