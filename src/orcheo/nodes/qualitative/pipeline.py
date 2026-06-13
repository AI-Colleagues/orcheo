"""Context, setup, ingest, validation, and output nodes for qualitative flows.

These nodes keep a shared shape and are specialised per workflow purely through
init arguments (result-key wiring, classification mode, messages), so the Theme
Analyst, Theme Coding Analyst, and Insight Reporter all reuse them.
"""

# ruff: noqa: C901, D102, PLR0912, PLR0915

from __future__ import annotations
from collections.abc import Mapping
from typing import Any, Literal
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import AINode, TaskNode
from orcheo.nodes.qualitative.accessors import (
    get_approved_codebook,
    get_code_assignments,
    get_configurable,
    get_pending_documents,
    get_quality_report,
    get_research_objective,
    get_seed_codebook_from_file,
    get_source_payload,
    get_units,
    is_vacuous,
)
from orcheo.nodes.qualitative.codebook import (
    escape_markdown_table_cell,
    make_unit_id,
    parse_codebook_csv,
    recover_exportable_codebook,
)
from orcheo.nodes.qualitative.coded_data import (
    build_coded_data_csv,
    parse_coded_data_csv,
)
from orcheo.nodes.qualitative.constants import DEFAULT_BATCH_SIZE, MAX_CODING_BATCHES
from orcheo.nodes.qualitative.keys import QualitativeResultKeys
from orcheo.nodes.qualitative.models import Codebook, Unit
from orcheo.nodes.qualitative.sources import SourceParser
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.nodes.storage import build_csv, upload_attachment
from orcheo.runtime.results import node_result


@registry.register(
    NodeMetadata(
        name="ContextPreNode",
        description="Load file content and build a source hint for the router",
        category="workflow",
    )
)
class ContextPreNode(TaskNode):
    """Load uploaded documents and provide a short source hint."""

    result_keys: QualitativeResultKeys = Field(default_factory=QualitativeResultKeys)
    documents_input_key: str = "documents"
    source_hint_field: str = "source_hint"
    pending_documents_field: str = "pending_documents"

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        configurable = (config or {}).get("configurable") or {}
        attachment_resolver = configurable.get("attachment_resolver")
        attachment_scope = configurable.get("attachment_scope")
        inputs = state.get("inputs") if isinstance(state, Mapping) else {}
        result: dict[str, Any] = {}

        pending_docs = get_pending_documents(state, self.result_keys)
        if not pending_docs:
            documents = (
                inputs.get(self.documents_input_key)
                if isinstance(inputs, Mapping)
                else []
            )
            if isinstance(documents, list) and documents:
                pending: list[dict[str, Any]] = []
                for doc in documents:
                    if not isinstance(doc, Mapping):
                        continue
                    content: str = doc.get("content") or ""
                    filename = (
                        doc.get("filename")
                        or doc.get("name")
                        or doc.get("source")
                        or ""
                    )
                    storage_path = doc.get("storage_path")
                    attachment_id = doc.get("attachment_id")

                    if attachment_id and attachment_resolver and attachment_scope:
                        try:
                            payload = await attachment_resolver.load_attachment_bytes(
                                attachment_id, attachment_scope
                            )
                            for enc in ("utf-8", "latin-1"):
                                try:
                                    content = payload.content.decode(enc)
                                    break
                                except UnicodeDecodeError:
                                    continue
                            if not filename:
                                filename = getattr(payload, "name", "") or ""
                        except Exception:  # noqa: BLE001
                            pass

                    if not content and storage_path:
                        try:
                            with open(storage_path, "rb") as fh:
                                raw = fh.read()
                            for enc in ("utf-8", "latin-1"):
                                try:
                                    content = raw.decode(enc)
                                    break
                                except UnicodeDecodeError:
                                    continue
                        except Exception:  # noqa: BLE001
                            pass

                    if content:
                        pending.append(
                            {
                                "content": content,
                                "filename": filename or None,
                                "source_type": doc.get("source_type"),
                            }
                        )
                if pending:
                    pending_docs = pending
                    result[self.pending_documents_field] = pending

        if not pending_docs:
            result[self.source_hint_field] = "No files loaded yet."
            return result

        filenames = [d.get("filename") or "unnamed" for d in pending_docs]
        result[self.source_hint_field] = (
            f"{len(pending_docs)} file(s) loaded: {', '.join(filenames)}"
        )
        return result


@registry.register(
    NodeMetadata(
        name="SetupNode",
        description="Resolve research objective, source payload, and codebook",
        category="workflow",
    )
)
class SetupNode(TaskNode):
    """Resolve the research objective, source payload, and optional codebook."""

    result_keys: QualitativeResultKeys = Field(default_factory=QualitativeResultKeys)
    research_objective_input_key: str = "research_objective"
    research_objective_config_key: str = "research_objective"
    source_config_key: str = "source"
    source_type_config_key: str = "source_type"
    source_filename_config_key: str = "source_filename"
    documents_input_key: str = "documents"
    objective_field: str = "objective"

    resolve_objective: bool = True
    resolve_codebook: bool = False
    source_kind: Literal["raw_data", "coded_data"] = "raw_data"
    exclude_codebook_docs: bool = False
    flexible_columns: bool = False

    def _resolve_objective(
        self, state: State, config: RunnableConfig, result: dict[str, Any]
    ) -> None:
        objective = ""
        inputs = state.get("inputs") or {}
        if isinstance(inputs, Mapping):
            cand = inputs.get(self.research_objective_input_key)
            if isinstance(cand, str) and not is_vacuous(cand):
                objective = cand.strip()
        if not objective:
            cfg_objective = get_configurable(config).get(
                self.research_objective_config_key
            )
            objective = cfg_objective.strip() if isinstance(cfg_objective, str) else ""

        existing_objective = get_research_objective(state, self.result_keys)
        effective_objective = existing_objective or ""
        if objective and is_vacuous(existing_objective or ""):
            effective_objective = objective
        if effective_objective:
            result[self.result_keys.research_objective_field] = effective_objective
        result[self.objective_field] = effective_objective or "(not provided)"

    def _resolve_source(
        self, state: State, config: RunnableConfig
    ) -> dict[str, Any] | None:
        keys = self.result_keys
        if self.source_kind == "coded_data":
            for doc in get_pending_documents(state, keys):
                content = doc.get("content") or ""
                if content and parse_coded_data_csv(content) is not None:
                    return {"content": content, "filename": doc.get("filename")}
            return None

        if self.exclude_codebook_docs:
            for doc in get_pending_documents(state, keys):
                content = doc.get("content") or ""
                if not content:
                    continue
                if parse_codebook_csv(content, reject_coded_data=True) is not None:
                    continue
                payload = {
                    "source_type": doc.get("source_type"),
                    "content": content,
                    "storage_path": None,
                    "filename": doc.get("filename"),
                }
                records, source_type = SourceParser.parse_payload(
                    payload,
                    allow_additional_sources=True,
                    flexible_columns=self.flexible_columns,
                )
                if records:
                    return {**payload, "source_type": source_type}

        candidate = SourceParser.normalise_payload(
            state,
            config,
            source_config_key=self.source_config_key,
            source_type_key=self.source_type_config_key,
            source_filename_key=self.source_filename_config_key,
            documents_key=self.documents_input_key,
        )
        if candidate is None:
            for doc in get_pending_documents(state, keys):
                if doc.get("content"):
                    return {
                        "content": doc["content"],
                        "filename": doc.get("filename"),
                        "source_type": doc.get("source_type"),
                        "storage_path": None,
                    }
        return candidate

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        keys = self.result_keys
        result: dict[str, Any] = {}

        if self.resolve_objective:
            self._resolve_objective(state, config, result)

        if get_source_payload(state, keys) is None:
            candidate = self._resolve_source(state, config)
            if candidate:
                result[keys.source_payload_field] = candidate

        if self.resolve_codebook and get_approved_codebook(state, keys) is None:
            for doc in get_pending_documents(state, keys):
                content = doc.get("content") or ""
                codebook = parse_codebook_csv(
                    content, reject_coded_data=self.source_kind == "coded_data"
                )
                if codebook is not None:
                    result[keys.approved_codebook_field] = codebook.model_dump(
                        mode="json"
                    )
                    break

        return result


@registry.register(
    NodeMetadata(
        name="IngestNode",
        description="Parse the source payload into units",
        category="workflow",
    )
)
class IngestNode(TaskNode):
    """Parse the source payload into ``Unit`` records."""

    result_keys: QualitativeResultKeys = Field(default_factory=QualitativeResultKeys)
    source_type_field: str = "source_type"
    documents_input_key: str = "documents"
    allow_additional_sources: bool = True
    flexible_columns: bool = False
    require_codebook: bool = False
    missing_codebook_message: str = (
        "No codebook was found. Please upload a codebook CSV before coding."
    )
    no_records_message: str = (
        "No usable rows found in the source data. Please attach a CSV with a text "
        "column or a transcript."
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        keys = self.result_keys
        if self.require_codebook and get_approved_codebook(state, keys) is None:
            return {"assistant_message": self.missing_codebook_message, "halt": True}

        source_payload = get_source_payload(
            state, keys
        ) or SourceParser.normalise_payload(
            state, config, documents_key=self.documents_input_key
        )
        records, source_type = SourceParser.parse_payload(
            source_payload,
            allow_additional_sources=self.allow_additional_sources,
            flexible_columns=self.flexible_columns,
        )
        if not records:
            return {"assistant_message": self.no_records_message, "halt": True}

        units: list[Unit] = []
        for idx, record in enumerate(records, start=1):
            units.append(
                Unit(
                    unit_id=make_unit_id(idx),
                    record_id=record.record_id,
                    source=record.source,
                    speaker=record.speaker,
                    text=record.text,
                    original_text=record.text,
                    metadata=record.metadata,
                )
            )
        result: dict[str, Any] = {
            "unit_count": len(units),
            self.source_type_field: source_type,
            keys.units_field: [u.model_dump(mode="json") for u in units],
        }
        if source_payload is not None:
            result[keys.source_payload_field] = {
                **dict(source_payload),
                "source_type": source_type,
            }
        return result


@registry.register(
    NodeMetadata(
        name="FileValidatorNode",
        description="Validate uploaded source files and codebooks",
        category="workflow",
    )
)
class FileValidatorNode(AINode):
    """Validate uploaded files and classify them by role."""

    result_keys: QualitativeResultKeys = Field(default_factory=QualitativeResultKeys)
    data_file_kind: Literal["raw", "coded"] = "raw"
    require_codebook: bool = False
    single_data_file: bool = False
    flexible_columns: bool = False
    codebook_result_field: str | None = None
    codebook_role_label: str = "seed codebook"
    announce_seed_codebook: bool = True
    no_files_message: str = (
        "No files are loaded. Please upload your data file (CSV or transcript) "
        "before validating."
    )
    missing_data_message: str = ""
    ready_message: str = "Ready — call the next step."
    error_message: str = "Issues found — please fix the errors above before proceeding."
    seed_codebook_message: str = (
        "Seed codebook detected — the pipeline will run in hybrid mode, merging "
        "your codebook with emergent codes."
    )

    def _classify(
        self, content: str, filename: str
    ) -> tuple[str, dict[str, Any] | None, Codebook | None, str]:
        """Return (kind, data_payload, codebook, line) for one document."""
        if self.data_file_kind == "coded":
            coded = parse_coded_data_csv(content)
            if coded is not None:
                units, assignments, _ = coded
                total = sum(len(a.assignments) for a in assignments)
                payload: dict[str, Any] = {"content": content, "filename": filename}
                line = (
                    f"✓ `{filename}` — coded data "
                    f"({len(units)} units, {total} assignments)"
                )
                return "data", payload, None, line

        codebook = parse_codebook_csv(
            content, reject_coded_data=self.data_file_kind == "coded"
        )
        if codebook is not None:
            theme_count = len(codebook.themes)
            code_count = sum(len(t.subthemes) for t in codebook.themes)
            line = (
                f"✓ `{filename}` — {self.codebook_role_label} "
                f"({theme_count} themes, {code_count} codes)"
            )
            return "codebook", None, codebook, line

        if self.data_file_kind == "raw":
            payload = {
                "content": content,
                "filename": filename,
                "source_type": None,
                "storage_path": None,
            }
            records, source_type = SourceParser.parse_payload(
                payload,
                allow_additional_sources=True,
                flexible_columns=self.flexible_columns,
            )
            if records:
                line = (
                    f"✓ `{filename}` — {source_type} data file ({len(records)} records)"
                )
                return "data", {**payload, "source_type": source_type}, None, line

        line = f"✗ `{filename}` — unrecognised format"
        return "unknown", None, None, line

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        keys = self.result_keys
        pending = get_pending_documents(state, keys)
        if not pending:
            return {"assistant_message": self.no_files_message}

        result_lines: list[str] = ["## File Validation\n"]
        errors: list[str] = []
        data_payload: dict[str, Any] | None = None
        codebook_data: Codebook | None = None
        data_count = 0
        codebook_count = 0

        for doc in pending:
            content = doc.get("content", "")
            filename = doc.get("filename") or "unnamed"
            if not content:
                reason = doc.get("load_error") or "no readable content found"
                errors.append(f"'{filename}' — {reason}")
                result_lines.append(f"✗ `{filename}` — {reason}")
                continue
            kind, payload, codebook, line = self._classify(content, filename)
            result_lines.append(line)
            if kind == "data":
                data_payload = payload
                data_count += 1
            elif kind == "codebook":
                codebook_data = codebook
                codebook_count += 1
            else:
                errors.append(
                    f"'{filename}' — could not parse as a data file or codebook"
                )

        if data_count == 0 and self.missing_data_message:
            errors.append(self.missing_data_message)
        if self.single_data_file and data_count > 1:
            errors.append("Multiple data files were uploaded; please provide one.")
        if codebook_count > 1:
            errors.append(
                "Multiple codebook CSV files were uploaded; please provide one."
            )
        if self.require_codebook and codebook_data is None:
            errors.append("No valid codebook CSV was found.")

        is_valid = (
            data_payload is not None
            and not errors
            and (codebook_data is not None or not self.require_codebook)
        )

        nested: dict[str, Any] = {}
        if data_payload is not None and get_source_payload(state, keys) is None:
            nested[keys.source_payload_field] = data_payload
        if codebook_data is not None:
            target = self.codebook_result_field or keys.seed_codebook_field
            already = (
                get_seed_codebook_from_file(state, keys)
                if target == keys.seed_codebook_field
                else get_approved_codebook(state, keys)
            )
            if already is None:
                nested[target] = codebook_data.model_dump(mode="json")

        if errors:
            result_lines.append("\n**Errors:**")
            for err in errors:
                result_lines.append(f"- {err}")
        result_lines.append(
            f"\n**Status:** {self.ready_message if is_valid else self.error_message}"
        )
        if codebook_data is not None and self.announce_seed_codebook:
            result_lines.append(f"\n**{self.seed_codebook_message}**")

        result: dict[str, Any] = {"assistant_message": "\n".join(result_lines)}
        if nested:
            result["results"] = {self.name: nested}
        return result


@registry.register(
    NodeMetadata(
        name="CodebookOutputNode",
        description="Render the produced codebook as Markdown",
        category="workflow",
    )
)
class CodebookOutputNode(AINode):
    """Render the produced draft codebook as a Markdown table for review."""

    result_keys: QualitativeResultKeys = Field(default_factory=QualitativeResultKeys)
    title: str = "Theme Analyst"
    review_message: str = (
        "Please review the codebook above. You can request revisions by describing "
        "what to change, or approve it to proceed to export."
    )
    no_codebook_message: str = (
        "No codebook could be produced. Please check the source data and try again."
    )
    failed_ingest_message: str = "Ingest failed."
    ingest_node_name: str = "ingest"
    batch_size_config_key: str = "batch_size"
    default_batch_size: int = DEFAULT_BATCH_SIZE
    max_coding_batches: int = MAX_CODING_BATCHES

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        from orcheo.nodes.qualitative.accessors import get_draft_codebook

        early_halt = node_result(state, self.ingest_node_name)
        if early_halt.get("halt"):
            msg = early_halt.get("assistant_message", self.failed_ingest_message)
            return {"assistant_message": str(msg)}

        codebook = get_draft_codebook(state, self.result_keys)
        if codebook is None:
            return {"assistant_message": self.no_codebook_message}

        research_objective = get_research_objective(state, self.result_keys)
        lines = [f"# {self.title} - Draft Codebook\n"]
        if research_objective:
            lines.append(f"**Research objective:** {research_objective}\n")
        total_themes = len(codebook.themes)
        total_codes = sum(len(t.subthemes) for t in codebook.themes)
        lines.append(f"**Themes:** {total_themes} | **Codes:** {total_codes}\n")
        lines.extend(
            [
                "| Theme ID | Theme Title | Code ID | Code Title | Definition | "
                "Include | Exclude |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for theme in codebook.themes:
            for subtheme in theme.subthemes:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            escape_markdown_table_cell(theme.theme_id),
                            escape_markdown_table_cell(theme.title),
                            escape_markdown_table_cell(subtheme.code_id),
                            escape_markdown_table_cell(subtheme.title),
                            escape_markdown_table_cell(subtheme.definition),
                            escape_markdown_table_cell("; ".join(subtheme.include)),
                            escape_markdown_table_cell("; ".join(subtheme.exclude)),
                        ]
                    )
                    + " |"
                )

        unit_total = len(get_units(state, self.result_keys))
        batch_size = get_configurable(config).get(
            self.batch_size_config_key, self.default_batch_size
        )
        batch_size = int(batch_size) if batch_size else self.default_batch_size
        coded_unit_cap = self.max_coding_batches * batch_size
        if unit_total > coded_unit_cap:
            lines.append(
                f"\n> **Note:** {unit_total} units were ingested but only the first "
                f"{coded_unit_cap} were coded (per-run limit). Split the data into "
                "smaller files to code the remainder."
            )

        lines.append(f"\n{self.review_message}")
        return {"assistant_message": "\n".join(lines).strip()}


@registry.register(
    NodeMetadata(
        name="ExportCodebookNode",
        description="Export the current codebook to CSV",
        category="workflow",
    )
)
class ExportCodebookNode(AINode):
    """Export the current draft codebook as a downloadable CSV."""

    result_keys: QualitativeResultKeys = Field(default_factory=QualitativeResultKeys)
    export_filename: str = "codebook.csv"
    export_mime_type: str = "text/csv"
    export_title: str = "Codebook Export"
    export_summary_template: str = (
        "Your codebook has **{total_themes} themes** and **{total_codes} codes**."
    )
    missing_codebook_message: str = (
        "No codebook is available to export. Please generate and approve a "
        "codebook first."
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        from orcheo.nodes.qualitative.accessors import get_draft_codebook

        needs_persist = get_draft_codebook(state, self.result_keys) is None
        codebook = recover_exportable_codebook(state, self.result_keys)
        if codebook is None:
            return {"assistant_message": self.missing_codebook_message}

        csv_content = build_csv(
            [
                "theme_id",
                "theme_title",
                "code_id",
                "code_title",
                "definition",
                "include",
                "exclude",
            ],
            [
                [
                    theme.theme_id,
                    theme.title,
                    sub.code_id,
                    sub.title,
                    sub.definition,
                    "; ".join(sub.include),
                    "; ".join(sub.exclude),
                ]
                for theme in codebook.themes
                for sub in theme.subthemes
            ],
        )

        try:
            _, csv_url = await upload_attachment(
                config, csv_content, self.export_filename, self.export_mime_type
            )
        except RuntimeError as exc:
            return {"assistant_message": f"Export failed: {exc}"}

        total_themes = len(codebook.themes)
        total_codes = sum(len(t.subthemes) for t in codebook.themes)
        summary = self.export_summary_template.format(
            total_themes=total_themes, total_codes=total_codes
        )
        lines = [
            f"## {self.export_title}\n",
            f"{summary}\n",
            f"[Download {self.export_filename}]({csv_url})",
        ]
        result: dict[str, Any] = {"assistant_message": "\n".join(lines)}
        if needs_persist:
            result["results"] = {
                self.name: {
                    self.result_keys.draft_codebook_field: codebook.model_dump(
                        mode="json"
                    )
                }
            }
        return result


@registry.register(
    NodeMetadata(
        name="RecodeOutputNode",
        description="Render recoded data with a coded-data CSV download link",
        category="workflow",
    )
)
class RecodeOutputNode(AINode):
    """Render the recoded data as the workflow output with a CSV download."""

    result_keys: QualitativeResultKeys = Field(default_factory=QualitativeResultKeys)
    ingest_node_name: str = "ingest"
    recoder_finalize_node: str = "recoder_finalize"
    export_filename: str = "coded_data.csv"
    export_mime_type: str = "text/csv"
    title: str = "Theme Coding Analyst"
    batch_size_config_key: str = "batch_size"
    default_batch_size: int = DEFAULT_BATCH_SIZE

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        keys = self.result_keys
        early = node_result(state, self.ingest_node_name)
        if early.get("halt"):
            return {
                "assistant_message": str(
                    early.get("assistant_message", "Ingest failed.")
                )
            }

        codebook = get_approved_codebook(state, keys)
        assignments = get_code_assignments(state, keys)
        if not assignments:
            return {
                "assistant_message": (
                    "No code assignments produced. Please check the source data "
                    "and codebook."
                )
            }

        units = get_units(state, keys)
        csv_content, total_assignments = (
            build_coded_data_csv(units, assignments, codebook)
            if codebook is not None
            else ("", 0)
        )

        csv_url: str | None = None
        export_error: str | None = None
        if csv_content:
            try:
                _, csv_url = await upload_attachment(
                    config, csv_content, self.export_filename, self.export_mime_type
                )
            except RuntimeError as exc:
                export_error = str(exc)

        lines = [f"# {self.title} — Coding Complete\n"]
        lines.append(
            f"✅ Coded **{len(assignments)} unit(s)** with "
            f"**{total_assignments} code assignment(s)** against the codebook.\n"
        )
        if csv_url:
            lines.append(f"**[⬇ Download {self.export_filename}]({csv_url})**\n")
        elif export_error:
            lines.append(f"_Could not generate the download link: {export_error}_\n")

        report = get_quality_report(state, keys)
        if report:
            lines.append(
                f"**Quality:** {report.flagged_units}/{report.total_units}"
                " units flagged.\n"
            )

        finalize = node_result(state, self.recoder_finalize_node)
        total_batches = finalize.get("total_batches")
        batch_end_index = finalize.get("batch_end_index")
        if (
            isinstance(total_batches, int)
            and isinstance(batch_end_index, int)
            and batch_end_index < total_batches
        ):
            batch_size = get_configurable(config).get(
                self.batch_size_config_key, self.default_batch_size
            )
            lines.append(
                f"\n> **Note:** only the first {batch_end_index * batch_size} unit(s) "
                "were coded this run (per-turn limit). Call `recode_data` again to "
                "code the remainder."
            )

        return {
            "assistant_message": "\n".join(lines).strip(),
            "results": {self.name: {"coded_data_url": csv_url}},
        }


@registry.register(
    NodeMetadata(
        name="ExportCodedDataNode",
        description="Serialise coded units and assignments to a CSV download",
        category="workflow",
    )
)
class ExportCodedDataNode(AINode):
    """Serialise coded units and code assignments to a CSV download link."""

    result_keys: QualitativeResultKeys = Field(default_factory=QualitativeResultKeys)
    export_filename: str = "coded_data.csv"
    export_mime_type: str = "text/csv"
    export_title: str = "Coded Data Export"
    missing_data_message: str = (
        "No coded data is available to export. Please run `recode_data` first."
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        keys = self.result_keys
        codebook = get_approved_codebook(state, keys)
        units = get_units(state, keys)
        assignments = get_code_assignments(state, keys)
        if codebook is None or not units or not assignments:
            return {"assistant_message": self.missing_data_message}

        csv_content, total_assignments = build_coded_data_csv(
            units, assignments, codebook
        )
        try:
            _, csv_url = await upload_attachment(
                config, csv_content, self.export_filename, self.export_mime_type
            )
        except RuntimeError as exc:
            return {"assistant_message": f"Export failed: {exc}"}

        lines = [
            f"## {self.export_title}\n",
            f"Your coded data includes **{len(units)} units** and "
            f"**{total_assignments} code assignment(s)**.\n",
            f"[Download {self.export_filename}]({csv_url})",
        ]
        return {
            "assistant_message": "\n".join(lines),
            "results": {self.name: {"coded_data_url": csv_url}},
        }


__all__ = [
    "CodebookOutputNode",
    "ContextPreNode",
    "ExportCodebookNode",
    "ExportCodedDataNode",
    "FileValidatorNode",
    "IngestNode",
    "RecodeOutputNode",
    "SetupNode",
]
