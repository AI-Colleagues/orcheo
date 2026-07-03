"""Setup, ingest, validation, and output nodes for qualitative flows.

These nodes keep a shared shape and are specialised per workflow purely through
init arguments (result-key wiring, classification mode, messages), so the Theme
Analyst, Theme Coding Analyst, and Insight Reporter all reuse them.
"""

# ruff: noqa: C901, D102, PLR0912, PLR0915

from __future__ import annotations
from collections.abc import Mapping
from typing import Any, Literal
from langchain_core.runnables import RunnableConfig
from pydantic import Field, ValidationError
from orcheo.graph.state import State
from orcheo.nodes.base import AINode, TaskNode
from orcheo.nodes.qualitative.accessors import (
    coerce_model,
    coerce_model_list,
    coerce_pending_documents,
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


def _decode_attachment_content(raw: bytes) -> str | None:
    """Decode attachment bytes using the text encodings supported by workflows."""
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


@registry.register(
    NodeMetadata(
        name="LoadAttachmentNode",
        description="Resolve uploaded attachments into readable payloads",
        category="workflow",
    )
)
class LoadAttachmentNode(TaskNode):
    """Load attachment content from inputs, storage paths, or attachment resolver."""

    input_key: str = "documents"
    output_field: str = "attachments"

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        configurable = get_configurable(config)
        attachment_resolver = configurable.get("attachment_resolver")
        attachment_scope = configurable.get("attachment_scope")
        inputs = state.get("inputs") if isinstance(state, Mapping) else {}
        documents = inputs.get(self.input_key) if isinstance(inputs, Mapping) else []
        attachments: list[dict[str, Any]] = []

        if not isinstance(documents, list):
            return {self.output_field: attachments}

        for document in documents:
            if not isinstance(document, Mapping):
                continue
            filename_value = (
                document.get("filename")
                or document.get("name")
                or document.get("source")
                or ""
            )
            filename = str(filename_value) if filename_value else ""
            attachment_id = document.get("attachment_id")
            storage_path = document.get("storage_path")
            errors: list[str] = []
            source = "input"
            raw_content = document.get("content")
            content = raw_content if isinstance(raw_content, str) else ""
            content_type = document.get("content_type") or document.get("mime_type")

            if not content and attachment_id:
                if attachment_resolver is None or not attachment_scope:
                    errors.append("attachment resolver is unavailable")
                else:
                    try:
                        payload = await attachment_resolver.load_attachment_bytes(
                            attachment_id, attachment_scope
                        )
                        raw = getattr(payload, "content", b"")
                        decoded = (
                            _decode_attachment_content(raw)
                            if isinstance(raw, bytes)
                            else None
                        )
                        if decoded is None:
                            errors.append("attachment content is not readable text")
                        else:
                            content = decoded
                            source = "attachment_resolver"
                        filename = filename or getattr(payload, "name", "") or ""
                        content_type = (
                            content_type
                            or getattr(payload, "content_type", None)
                            or getattr(payload, "mime_type", None)
                        )
                    except Exception as exc:  # noqa: BLE001
                        errors.append(str(exc) or "failed to load attachment")

            if not content and storage_path:
                try:
                    with open(storage_path, "rb") as fh:
                        raw = fh.read()
                    decoded = _decode_attachment_content(raw)
                    if decoded is None:
                        errors.append("stored attachment is not readable text")
                    else:
                        content = decoded
                        source = "storage"
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc) or "failed to load stored attachment")

            if not content and not errors:
                errors.append("no readable content found")

            attachments.append(
                {
                    "filename": filename or "unnamed",
                    "content": content,
                    "content_type": content_type,
                    "source_type": document.get("source_type"),
                    "source": source,
                    "attachment_id": attachment_id,
                    "storage_path": storage_path,
                    "errors": errors,
                }
            )

        return {self.output_field: attachments}


@registry.register(
    NodeMetadata(
        name="ValidateFilesNode",
        description="Validate qualitative data files and optional codebooks",
        category="workflow",
    )
)
class ValidateFilesNode(TaskNode):
    """Validate loaded qualitative files and return a minimal normalized payload."""

    attachments_node_name: str = "load_attachments"
    attachments_field: str = "attachments"
    data_kind: Literal["raw", "coded"] = "raw"
    require_codebook: bool = False
    flexible_columns: bool = False

    def _attachments(self, state: State) -> list[dict[str, Any]]:
        result = node_result(state, self.attachments_node_name)
        attachments = result.get(self.attachments_field)
        if not isinstance(attachments, list):
            return []
        return [dict(item) for item in attachments if isinstance(item, Mapping)]

    def _classify_raw_data(
        self, content: str, filename: str, source_type: str | None
    ) -> dict[str, Any] | None:
        payload = {
            "filename": filename,
            "content": content,
            "source_type": source_type,
            "storage_path": None,
        }
        records, source_type = SourceParser.parse_payload(
            payload,
            allow_additional_sources=True,
            flexible_columns=self.flexible_columns,
        )
        if not records:
            return None
        return {
            "filename": filename,
            "kind": "raw",
            "source_type": source_type,
            "record_count": len(records),
        }

    def _classify_coded_data(
        self, content: str, filename: str
    ) -> dict[str, Any] | None:
        parsed = parse_coded_data_csv(content)
        if parsed is None:
            return None
        units, assignments, _ = parsed
        assignment_count = sum(len(item.assignments) for item in assignments)
        return {
            "filename": filename,
            "kind": "coded",
            "unit_count": len(units),
            "assignment_count": assignment_count,
        }

    def _assistant_message(self, nested: Mapping[str, Any]) -> str:
        errors = nested.get("errors")
        if isinstance(errors, list) and errors:
            lines = ["File validation failed."]
            lines.extend(f"- {error}" for error in errors)
            return "\n".join(lines)

        data_file = nested.get("data_file")
        codebook_file = nested.get("codebook_file")
        parts: list[str] = []
        if isinstance(data_file, Mapping):
            filename = str(data_file.get("filename") or "data file")
            kind = data_file.get("kind")
            if kind == "raw":
                source_type = data_file.get("source_type")
                record_count = data_file.get("record_count")
                detail = f"{record_count} record(s)"
                if source_type:
                    detail = f"{detail}, {source_type}"
                parts.append(f"data file `{filename}` ({detail})")
            elif kind == "coded":
                unit_count = data_file.get("unit_count")
                assignment_count = data_file.get("assignment_count")
                parts.append(
                    f"coded data file `{filename}` "
                    f"({unit_count} unit(s), {assignment_count} assignment(s))"
                )
            else:
                parts.append(f"data file `{filename}`")
        if isinstance(codebook_file, Mapping):
            filename = str(codebook_file.get("filename") or "codebook")
            parts.append(f"codebook `{filename}`")

        if not parts:
            return "Files look valid."
        return f"Files look valid: found {' and '.join(parts)}."

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        attachments = self._attachments(state)
        errors: list[str] = []
        data_files: list[dict[str, Any]] = []
        codebook_files: list[dict[str, Any]] = []

        if not attachments:
            errors.append("No attachments were loaded.")

        for attachment in attachments:
            filename = str(attachment.get("filename") or "unnamed")
            attachment_errors = attachment.get("errors")
            if isinstance(attachment_errors, list) and attachment_errors:
                errors.extend(f"{filename}: {err}" for err in attachment_errors)
                continue

            content = attachment.get("content")
            if not isinstance(content, str) or not content.strip():
                errors.append(f"{filename}: no readable content found")
                continue

            coded_file = self._classify_coded_data(content, filename)
            if coded_file is not None:
                if self.data_kind == "coded":
                    data_files.append(coded_file)
                else:
                    errors.append(
                        f"{filename}: coded data was uploaded, but raw data is expected"
                    )
                continue

            codebook = parse_codebook_csv(content, reject_coded_data=True)
            if codebook is not None:
                codebook_files.append({"filename": filename, "present": True})
                continue

            if self.data_kind == "raw":
                raw_source_type = attachment.get("source_type")
                raw_file = self._classify_raw_data(
                    content,
                    filename,
                    raw_source_type if isinstance(raw_source_type, str) else None,
                )
                if raw_file is not None:
                    data_files.append(raw_file)
                    continue

            expected = "coded data CSV" if self.data_kind == "coded" else "raw data"
            errors.append(f"{filename}: could not parse as {expected} or codebook CSV")

        if not data_files:
            errors.append("No valid data file found.")
        elif len(data_files) > 1:
            errors.append("Multiple data files found; provide exactly one.")

        if len(codebook_files) > 1:
            errors.append("Multiple codebook files found; provide at most one.")
        if self.require_codebook and not codebook_files:
            errors.append("No valid codebook CSV found.")

        ok = not errors
        nested: dict[str, Any] = {"ok": ok, "errors": errors}
        if len(data_files) == 1:
            nested["data_file"] = data_files[0]
        if len(codebook_files) == 1:
            nested["codebook_file"] = codebook_files[0]
        nested["assistant_message"] = self._assistant_message(nested)

        return nested


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
    resolve_seed_codebook: bool = False
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
            for doc in get_pending_documents(state, keys):  # pragma: no branch
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
                if records:  # pragma: no branch
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

        if (
            self.resolve_seed_codebook
            and get_seed_codebook_from_file(state, keys) is None
        ):
            for doc in get_pending_documents(state, keys):
                content = doc.get("content") or ""
                codebook = parse_codebook_csv(content, reject_coded_data=True)
                if codebook is not None:
                    result[keys.seed_codebook_field] = codebook.model_dump(mode="json")
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
    source_payload: Any | None = None
    pending_documents: Any | None = None
    approved_codebook: Any | None = None
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
        approved_codebook = coerce_model(self.approved_codebook, Codebook)
        if approved_codebook is None:
            approved_codebook = get_approved_codebook(state, keys)
        if self.require_codebook and approved_codebook is None:
            return {"assistant_message": self.missing_codebook_message, "halt": True}

        source_payload = (
            dict(self.source_payload)
            if isinstance(self.source_payload, Mapping)
            else None
        )
        if source_payload is None:
            source_payload = get_source_payload(state, keys)
        if source_payload is None:
            source_payload = SourceParser.normalise_payload(
                state, config, documents_key=self.documents_input_key
            )
        records, source_type = SourceParser.parse_payload(
            source_payload,
            allow_additional_sources=self.allow_additional_sources,
            flexible_columns=self.flexible_columns,
        )
        if not records:
            pending_documents = coerce_pending_documents(self.pending_documents)
            if not pending_documents:
                pending_documents = get_pending_documents(state, keys)
            for doc in pending_documents:
                content = doc.get("content") or ""
                if not content:
                    continue
                payload = {
                    "source_type": doc.get("source_type"),
                    "content": content,
                    "storage_path": None,
                    "filename": doc.get("filename"),
                }
                records, source_type = SourceParser.parse_payload(
                    payload,
                    allow_additional_sources=self.allow_additional_sources,
                    flexible_columns=self.flexible_columns,
                )
                if records:
                    source_payload = {**payload, "source_type": source_type}
                    break
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
            "halt": False,
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
        name="CodebookOutputNode",
        description="Render the produced codebook as Markdown",
        category="workflow",
    )
)
class CodebookOutputNode(AINode):
    """Render the produced draft codebook as a Markdown table for review."""

    result_keys: QualitativeResultKeys = Field(default_factory=QualitativeResultKeys)
    codebook: Any | None = None
    research_objective: str | None = None
    units: Any | None = None
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

        codebook = coerce_model(self.codebook, Codebook)
        if codebook is None:
            codebook = get_draft_codebook(state, self.result_keys)
        if codebook is None:
            return {"assistant_message": self.no_codebook_message}

        research_objective = self.research_objective
        if not research_objective:
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

        units = coerce_model_list(self.units, Unit)
        if not units:
            units = get_units(state, self.result_keys)
        unit_total = len(units)
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
    """Export a configured codebook as a downloadable CSV."""

    codebook: Codebook | str
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

    def _resolved_codebook(self) -> Codebook | None:
        raw: Any = self.codebook
        if isinstance(raw, Codebook):
            return raw
        if isinstance(raw, Mapping):
            try:
                return Codebook.model_validate(raw)
            except ValidationError:
                return None
        return None

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        codebook = self._resolved_codebook()
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
        return {"assistant_message": "\n".join(lines)}


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
    "ExportCodebookNode",
    "ExportCodedDataNode",
    "IngestNode",
    "LoadAttachmentNode",
    "RecodeOutputNode",
    "SetupNode",
    "ValidateFilesNode",
]
