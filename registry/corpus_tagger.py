"""Batch corpus tagger — tags entries with usage metadata via firewall checks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from registry.firewall import ContaminationFirewall, FirewallResult


@dataclass
class TaggingReport:
    """Statistics from a corpus tagging run."""

    total_entries: int = 0
    passed: int = 0
    rejected_evaluation_source: int = 0
    rejected_holdout_partition: int = 0
    rejected_similarity: int = 0
    rejected_cross_source: int = 0

    @property
    def total_rejected(self) -> int:
        return (
            self.rejected_evaluation_source
            + self.rejected_holdout_partition
            + self.rejected_similarity
            + self.rejected_cross_source
        )

    def summary(self) -> str:
        """Return a human-readable summary."""
        lines = [
            f"Total entries:              {self.total_entries}",
            f"Passed:                     {self.passed}",
            f"Rejected (eval source):     {self.rejected_evaluation_source}",
            f"Rejected (holdout):         {self.rejected_holdout_partition}",
            f"Rejected (similarity):      {self.rejected_similarity}",
            f"Rejected (cross-source):    {self.rejected_cross_source}",
            f"Total rejected:             {self.total_rejected}",
        ]
        return "\n".join(lines)


_REASON_TO_FIELD = {
    "evaluation_source": "rejected_evaluation_source",
    "holdout_partition": "rejected_holdout_partition",
    "similarity_threshold": "rejected_similarity",
    "cross_source_duplicate": "rejected_cross_source",
}


class CorpusTagger:
    """Tags corpus entries with usage metadata and runs firewall checks.

    Reads a JSONL corpus file, checks each entry against the firewall,
    and writes tagged entries to output. Rejected entries go to a separate file.
    """

    def __init__(self, firewall: ContaminationFirewall):
        self._firewall = firewall

    def process_corpus(
        self, input_path: Path, output_path: Path, rejected_path: Path
    ) -> TaggingReport:
        """Process a full corpus file.

        Returns TaggingReport with counts of passed/rejected by reason.
        """
        report = TaggingReport()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        rejected_path.parent.mkdir(parents=True, exist_ok=True)

        with (
            open(input_path) as fin,
            open(output_path, "w") as fout,
            open(rejected_path, "w") as frej,
        ):
            for line in fin:
                line = line.strip()
                if not line:
                    continue

                entry = json.loads(line)
                report.total_entries += 1

                result: FirewallResult = self._firewall.check_entry(entry)

                if result.passed:
                    tagged = self._firewall.tag_entry(entry)
                    fout.write(json.dumps(tagged, ensure_ascii=False) + "\n")
                    report.passed += 1
                else:
                    # Tag the entry with rejection info
                    entry["firewall_check"] = "fail"
                    entry["firewall_reason"] = result.reason
                    entry["firewall_details"] = result.details
                    if result.similarity_score is not None:
                        entry["firewall_similarity_score"] = result.similarity_score
                    if result.matched_source is not None:
                        entry["firewall_matched_source"] = result.matched_source
                    frej.write(json.dumps(entry, ensure_ascii=False) + "\n")

                    attr = _REASON_TO_FIELD.get(result.reason)
                    if attr:
                        setattr(report, attr, getattr(report, attr) + 1)

        return report
