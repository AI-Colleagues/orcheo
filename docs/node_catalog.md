# Built-in Node Catalog

Orcheo currently ships with **145 built-in nodes** across **21 categories**.

This catalog is sourced from runtime node registry metadata. Run `orcheo node list` to inspect the nodes available in your environment, including custom registrations.

## Summary

| Category | Node Count |
|---|---:|
| AI (`ai`) | 7 |
| Agentensor (`agentensor`) | 1 |
| Base (`base`) | 1 |
| Browser (`browser`) | 6 |
| Communication (`communication`) | 2 |
| Conversational Search (`conversational_search`) | 46 |
| Data (`data`) | 6 |
| Evaluation (`evaluation`) | 8 |
| Lark (`lark`) | 2 |
| LinkedIn (`linkedin`) | 1 |
| Logic (`logic`) | 3 |
| Messaging (`messaging`) | 6 |
| MongoDB (`mongodb`) | 9 |
| RSS (`rss`) | 1 |
| Slack (`slack`) | 2 |
| Storage (`storage`) | 3 |
| Telegram (`telegram`) | 1 |
| Trigger (`trigger`) | 9 |
| Utility (`utility`) | 5 |
| WeCom (`wecom`) | 9 |
| Workflow (`workflow`) | 17 |

## AI Nodes

| Node | Description |
|---|---|
| **AgentNode** | Execute an AI agent with tools |
| **AgentReplyExtractorNode** | Extract the final assistant reply from agent messages |
| **AntigravityNode** | Execute the Antigravity CLI as a non-interactive coding-agent step. Runs unsandboxed with the worker's privileges; trusted workflows only. |
| **ClaudeCodeNode** | Execute Claude Code as a non-interactive coding-agent step. Runs unsandboxed with the worker's privileges; trusted workflows only. |
| **CodexNode** | Execute the Codex CLI as a non-interactive coding-agent step. Runs unsandboxed with the worker's privileges; trusted workflows only. |
| **DeepAgentNode** | Execute an autonomous deep-research agent with configurable iteration depth for multi-step tool use and synthesis |
| **LLMNode** | Execute a text-only LLM call |

## Agentensor Nodes

| Node | Description |
|---|---|
| **AgentensorNode** | Evaluate or train agent prompts using Agentensor datasets and evaluators. |

## Base Nodes

| Node | Description |
|---|---|
| **NoOpTaskNode** | A no-op node for developers to use as a template for custom nodes. Do not use this node directly, but inherit from this with your own `run` method. |

## Browser Nodes

| Node | Description |
|---|---|
| **BrowserActionNode** | Perform a user-style action against the active Playwright page. |
| **BrowserCloseNode** | Close an active Playwright browser session. |
| **BrowserExtractNode** | Extract page or element content from the active Playwright page. |
| **BrowserNavigateNode** | Open a URL in a shared Playwright browser session. |
| **BrowserScriptNode** | Evaluate JavaScript in the active Playwright page context. |
| **BrowserWaitNode** | Wait for a condition on the active Playwright page. |

## Communication Nodes

| Node | Description |
|---|---|
| **DiscordWebhookNode** | Send messages to Discord via incoming webhooks. |
| **EmailNode** | Send an email via SMTP with optional TLS and authentication. |

## Conversational Search Nodes

| Node | Description |
|---|---|
| **ABTestingNode** | Rank variants and gate rollouts using evaluation metrics. |
| **AnalyticsExportNode** | Aggregate evaluation metrics and feedback for export. |
| **AnswerCachingNode** | Cache answers by query with TTL-based eviction. |
| **AnswerQualityEvaluationNode** | Score generated answers against reference answers. |
| **ChunkEmbeddingNode** | Generate vector records for document chunks via configurable embedding functions. |
| **ChunkingStrategyNode** | Split documents into overlapping chunks for indexing. |
| **CitationsFormatterNode** | Format citation metadata into human-readable strings. |
| **ContextCompressorNode** | Summarize retrieved context using an AI model so downstream nodes can consume a condensed evidence block. |
| **ConversationCompressorNode** | Summarize and budget a conversation history for downstream use. |
| **ConversationStateNode** | Load and persist conversation history for a session. |
| **CoreferenceResolverNode** | Resolve simple pronouns using prior conversation turns. |
| **DataAugmentationNode** | Generate synthetic variants of dataset entries. |
| **DatasetNode** | Load and filter golden datasets for evaluation workflows. |
| **DenseSearchNode** | Perform embedding-based retrieval via a configured vector store. |
| **DocumentLoaderNode** | Normalize raw document payloads into validated Document objects. |
| **FailureAnalysisNode** | Categorize evaluation failures for triage. |
| **FeedbackIngestionNode** | Persist feedback entries with deduplication. |
| **GroundedGeneratorNode** | Generate grounded answers with citations and retry semantics. |
| **HallucinationGuardNode** | Validate generator output for citations and completeness. |
| **HybridFusionNode** | Fuse results from multiple retrievers using RRF or weighted sum. |
| **IncrementalIndexerNode** | Index or update chunks incrementally with retry and backpressure controls. |
| **LLMJudgeNode** | Apply lightweight, AI model judging heuristics. |
| **MemoryPrivacyNode** | Enforce redaction and retention for conversation history. |
| **MemorySummarizerNode** | Persist a compact conversation summary into the memory store. |
| **MetadataExtractorNode** | Attach structured metadata to normalized documents. |
| **MultiHopPlannerNode** | Derive sequential sub-queries for multi-hop answering. |
| **PineconeRerankNode** | Rerank retrieval results via Pinecone inference for tighter ordering. |
| **PolicyComplianceNode** | Apply policy checks and emit audit details. |
| **QueryClarificationNode** | Generate clarifying prompts when ambiguity is detected. |
| **QueryClassifierNode** | Classify a query intent to support routing decisions. |
| **QueryRewriteNode** | Rewrite or expand a query using recent conversation context to improve recall. |
| **ReRankerNode** | Apply secondary scoring to retrieval results for better ranking. |
| **RetrievalEvaluationNode** | Compute retrieval quality metrics for search results. |
| **SearchResultAdapterNode** | Normalize arbitrary retrieval payloads into SearchResult items. |
| **SearchResultFormatterNode** | Format SearchResult entries into markdown for tool responses. |
| **SessionManagementNode** | Manage conversation sessions with capacity controls. |
| **SourceRouterNode** | Route fused results into per-source buckets with filtering. |
| **SparseSearchNode** | Perform sparse keyword retrieval using BM25 scoring. |
| **StreamingGeneratorNode** | Generate responses and stream token chunks with backpressure. |
| **TextEmbeddingNode** | Embed one or more text inputs using a configurable embedding model. |
| **TopicShiftDetectorNode** | Detect whether a new query diverges from recent conversation context. |
| **TurnAnnotationNode** | Annotate conversation turns with heuristics. |
| **UserFeedbackCollectionNode** | Normalize and validate explicit user feedback. |
| **VectorStoreUpsertNode** | Persist vector records produced by an embedding node into storage. |
| **WebDocumentLoaderNode** | Fetch web pages and convert them to normalized Document objects. |
| **WebSearchNode** | Perform live web search via the Tavily API. |

## Data Nodes

| Node | Description |
|---|---|
| **CodeNode** | Base class for user-authored node logic; the sole customisation port. Do not use this node directly, but inherit from this with your own `run` method. |
| **DataTransformNode** | Map values from an input payload into a transformed structure. |
| **HtmlTextTransformNode** | Decode, escape, and normalise HTML text in structured data. |
| **HttpRequestNode** | Perform an HTTP request and return the response payload. |
| **JsonProcessingNode** | Parse, stringify, or extract data from JSON payloads. |
| **MergeNode** | Merge multiple payloads into a single aggregate structure. |

## Evaluation Nodes

| Node | Description |
|---|---|
| **BleuMetricsNode** | Compute SacreBLEU between predicted and reference texts |
| **ConversationalBatchEvalNode** | Iterate conversations and turns through a pipeline, collecting predictions paired with gold labels |
| **MultiDoc2DialCorpusLoaderNode** | Load MultiDoc2Dial corpus documents from a local path or URL and normalize them for indexing. |
| **MultiDoc2DialDatasetNode** | Load MultiDoc2Dial conversations with gold responses for evaluation |
| **QReCCDatasetNode** | Load QReCC conversations with gold rewrites for evaluation |
| **RougeMetricsNode** | Compute ROUGE scores between predicted and reference texts |
| **SemanticSimilarityMetricsNode** | Compute embedding cosine similarity between predicted and reference texts |
| **TokenF1MetricsNode** | Compute token-level F1 between predicted and reference texts |

## Lark Nodes

| Node | Description |
|---|---|
| **LarkSendMessageNode** | Send messages to Lark chats or reply to inbound Lark messages |
| **LarkTenantAccessTokenNode** | Fetch a tenant access token from the Lark auth API |

## LinkedIn Nodes

| Node | Description |
|---|---|
| **LinkedInPostNode** | Create a LinkedIn post using vault-backed access credentials. |

## Logic Nodes

| Node | Description |
|---|---|
| **ExtractAIMessageNode** | Extract a text AI message from a structured response |
| **HumanInputNode** | Pause a workflow and collect human input |
| **StructuredRouterDispatchNode** | Translate structured agent output into a routing result |

## Messaging Nodes

| Node | Description |
|---|---|
| **MessageDiscordNode** | Send a message to a Discord channel using a bot token. |
| **MessageQQNode** | Send a message to QQ using AppID and client secret. |
| **MessageTelegramNode** | Send message to Telegram |
| **TelegramSendDocumentNode** | Send a document file to Telegram |
| **WechatReplyNode** | Reply to WeChat messages through OpenClaw HTTP APIs. |
| **WeixinReplyNode** | Backwards-compatible alias for WechatReplyNode. |

## MongoDB Nodes

| Node | Description |
|---|---|
| **MongoDBAggregateNode** | MongoDB aggregate wrapper |
| **MongoDBEnsureSearchIndexNode** | Ensure a MongoDB Atlas Search index exists. |
| **MongoDBEnsureVectorIndexNode** | Ensure a MongoDB Atlas vector search index exists. |
| **MongoDBFindNode** | MongoDB find wrapper with sort and limit support |
| **MongoDBHybridSearchNode** | Execute a hybrid search over text and vector indexes asynchronously. |
| **MongoDBInsertManyNode** | Insert documents into MongoDB with optional vectors |
| **MongoDBNode** | MongoDB node |
| **MongoDBUpdateManyNode** | MongoDB update_many wrapper |
| **MongoDBUpsertManyNode** | Bulk-upsert upstream records into MongoDB using keyed filters |

## RSS Nodes

| Node | Description |
|---|---|
| **RSSNode** | Fetch and parse RSS/Atom feeds from multiple sources |

## Slack Nodes

| Node | Description |
|---|---|
| **SlackEventsParserNode** | Validate and parse Slack Events API payloads |
| **SlackNode** | Slack node |

## Storage Nodes

| Node | Description |
|---|---|
| **FileUploadNode** | Upload a single file to blob storage and return a download URL. Reads content, filename, and mime_type from state via template references and writes attachment_id and download_url to node_results. |
| **GraphStoreAppendMessageNode** | Append a message to a versioned chat history item in the LangGraph graph store. |
| **PostgresNode** | Execute SQL against a PostgreSQL database using psycopg. |

## Telegram Nodes

| Node | Description |
|---|---|
| **TelegramEventsParserNode** | Validate and parse Telegram Bot webhook updates |

## Trigger Nodes

| Node | Description |
|---|---|
| **CronTriggerNode** | Configure a cron-based schedule trigger. |
| **DiscordBotListenerNode** | Receive Discord bot messages through the Gateway. |
| **HttpPollingTriggerNode** | Poll an HTTP endpoint on an interval to trigger runs. |
| **ManualTriggerNode** | Trigger workflows manually from the dashboard. |
| **QQBotListenerNode** | Receive QQ bot messages through the managed Gateway. |
| **TelegramBotListenerNode** | Receive Telegram bot updates through managed long polling. |
| **WebhookTriggerNode** | Configure an HTTP webhook trigger. |
| **WechatListenerPluginNode** | Receive WeChat events through the plugin contract. |
| **WeixinListenerPluginNode** | Backwards-compatible alias for WechatListenerPluginNode. |

## Utility Nodes

| Node | Description |
|---|---|
| **DebugNode** | Capture state snapshots and emit debug information. |
| **DelayNode** | Pause execution for a fixed duration |
| **ForLoopNode** | Iterate over a list, exposing one item per invocation |
| **SetVariableNode** | Store variables for downstream nodes |
| **SubWorkflowNode** | Execute a mini workflow inline using the node registry. |

## WeCom Nodes

| Node | Description |
|---|---|
| **WeComAIBotEventsParserNode** | Validate WeCom AI bot signatures and parse callbacks |
| **WeComAIBotPassiveReplyNode** | Encrypt and return passive AI bot replies |
| **WeComAIBotResponseNode** | Send active replies to WeCom AI bot response_url |
| **WeComAccessTokenNode** | Fetch and cache WeCom access token |
| **WeComCustomerServiceSendNode** | Send messages via WeCom Customer Service (微信客服) |
| **WeComCustomerServiceSyncNode** | Sync messages from WeCom Customer Service (微信客服) |
| **WeComEventsParserNode** | Validate WeCom signatures and parse callback payloads |
| **WeComGroupPushNode** | Send messages to WeCom group via webhook |
| **WeComSendMessageNode** | Send messages to WeCom chat |

## Workflow Nodes

| Node | Description |
|---|---|
| **CodebookOutputNode** | Render the produced codebook as Markdown |
| **CodedDataIngestNode** | Parse a coded-data CSV into units, assignments, quantification |
| **DataQualityNode** | Flag low-effort, duplicate, PII, and AI-like responses |
| **ExportCodebookNode** | Export the current codebook to CSV or inline JSON text |
| **ExportCodedDataNode** | Serialise coded units and assignments to a CSV download |
| **ExportReportNode** | Regenerate the downloadable Markdown report link |
| **IngestNode** | Parse the source payload into units |
| **InsightCriticNode** | Find counter-evidence and annotate candidate insights |
| **LLMStageFinalizeNode** | Persist a qualitative LLM stage response |
| **LLMStagePrepareNode** | Prepare prompt payloads for a qualitative LLM stage |
| **LoadAttachmentsNode** | Resolve uploaded attachments into readable payloads |
| **RecodeOutputNode** | Render recoded data with a coded-data CSV download link |
| **RecommendationGeneratorNode** | Attach Finding -> Action -> Expected impact recommendations |
| **ReportOutputNode** | Render the final report and return it with a download link |
| **SegmentRecordsNode** | Segment structured records into qualitative coding units |
| **TwoTrackThemeReportNode** | Render a Covered/Emergent salience-ranked theme report |
| **ValidateFilesNode** | Validate qualitative data files and optional codebooks |

## Creating Custom Nodes

See the [Plugin Author Guide](custom_nodes_and_tools.md) for instructions on creating and registering plugin-managed nodes.
