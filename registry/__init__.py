"""Task source registry, split protocol, contamination firewall, and downloaders."""

from registry.source_registry import (
    REGISTRY,
    TaskSource,
    get_evaluation_sources,
    get_source,
    get_sources_by_category,
    get_split_sources,
    get_training_sources,
)
from registry.split_protocol import HoldoutManifest, SplitProtocol
from registry.firewall import ContaminationFirewall, FirewallResult
from registry.corpus_tagger import CorpusTagger, TaggingReport
from registry.downloaders import DatasetDownloader, DatasetNotCached
from registry.task_extractor import ExtractedTask, TaskExtractor

__all__ = [
    "ContaminationFirewall",
    "CorpusTagger",
    "DatasetDownloader",
    "DatasetNotCached",
    "ExtractedTask",
    "FirewallResult",
    "HoldoutManifest",
    "REGISTRY",
    "SplitProtocol",
    "TaggingReport",
    "TaskExtractor",
    "TaskSource",
    "get_evaluation_sources",
    "get_source",
    "get_sources_by_category",
    "get_split_sources",
    "get_training_sources",
]
