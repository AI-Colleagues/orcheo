# Built-in Node Catalog

Orcheo currently ships with **97 built-in nodes** across **14 categories**.

This catalog is sourced from runtime node registry metadata. Run `orcheo node list` to inspect the nodes available in your environment, including custom registrations.

## Summary

| Category | Node Count |
|---|---:|
| Agentensor (`agentensor`) | 1 |
| AI (`ai`) | 4 |
| Base (`base`) | 1 |
| Communication (`communication`) | 2 |
| Conversational Search (`conversational_search`) | 46 |
| Data (`data`) | 4 |
| Evaluation (`evaluation`) | 8 |
| Messaging (`messaging`) | 1 |
| MongoDB (`mongodb`) | 8 |
| Slack (`slack`) | 2 |
| Storage (`storage`) | 2 |
| Trigger (`trigger`) | 4 |
| Utility (`utility`) | 5 |
| WeCom (`wecom`) | 9 |

## Agentensor Nodes

| Node | Description |
|---|---|
| **AgentensorNode** | Evaluate or train agent prompts using Agentensor datasets and evaluators. |

## AI Nodes

| Node | Description |
|---|---|
| **AgentNode** | Execute an AI agent with tools |
| **LLMNode** | Execute a text-only LLM call |

## Base Nodes

| Node | Description |
|---|---|
| **NoOpTaskNode** | A no-op node for developers to use as a template for custom nodes. Do not use this node directly, but inherit from this with your own `run` method. |

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
| **DataTransformNode** | Map values from an input payload into a transformed structure. |
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

## Messaging Nodes

| Node | Description |
|---|---|
| **MessageTelegram** | Send message to Telegram |

## MongoDB Nodes

| Node | Description |
|---|---|
| **MongoDBAggregateNode** | MongoDB aggregate wrapper |
| **MongoDBEnsureSearchIndexNode** | Ensure a MongoDB Atlas Search index exists. |
| **MongoDBEnsureVectorIndexNode** | Ensure a MongoDB Atlas vector search index exists. |
| **MongoDBFindNode** | MongoDB find wrapper with sort and limit support |
| **MongoDBHybridSearchNode** | Execute a hybrid search over text and vector indexes. |
| **MongoDBInsertManyNode** | Insert documents into MongoDB with optional vectors |
| **MongoDBNode** | MongoDB node |
| **MongoDBUpdateManyNode** | MongoDB update_many wrapper |

## Slack Nodes

| Node | Description |
|---|---|
| **SlackEventsParserNode** | Validate and parse Slack Events API payloads |
| **SlackNode** | Slack node |

## Storage Nodes

| Node | Description |
|---|---|
| **PostgresNode** | Execute SQL against a PostgreSQL database using psycopg. |
| **SQLiteNode** | Execute SQL statements against a SQLite database. |

## Trigger Nodes

| Node | Description |
|---|---|
| **CronTriggerNode** | Configure a cron-based schedule trigger. |
| **HttpPollingTriggerNode** | Poll an HTTP endpoint on an interval to trigger runs. |
| **ManualTriggerNode** | Trigger workflows manually from the dashboard. |
| **WebhookTriggerNode** | Configure an HTTP webhook trigger. |

## Utility Nodes

| Node | Description |
|---|---|
| **DebugNode** | Capture state snapshots and emit debug information. |
| **DelayNode** | Pause execution for a fixed duration |
| **SetVariableNode** | Store variables for downstream nodes |
| **SubWorkflowNode** | Execute a mini workflow inline using the node registry. |

## WeCom Nodes

| Node | Description |
|---|---|
| **WeComAccessTokenNode** | Fetch and cache WeCom access token |
| **WeComAIBotEventsParserNode** | Validate WeCom AI bot signatures and parse callbacks |
| **WeComAIBotPassiveReplyNode** | Encrypt and return passive AI bot replies |
| **WeComAIBotResponseNode** | Send active replies to WeCom AI bot response_url |
| **WeComCustomerServiceSendNode** | Send messages via WeCom Customer Service (微信客服) |
| **WeComCustomerServiceSyncNode** | Sync messages from WeCom Customer Service (微信客服) |
| **WeComEventsParserNode** | Validate WeCom signatures and parse callback payloads |
| **WeComGroupPushNode** | Send messages to WeCom group via webhook |
| **WeComSendMessageNode** | Send messages to WeCom chat |

## Creating Custom Nodes

See the [Plugin Author Guide](custom_nodes_and_tools.md) for instructions on creating and registering plugin-managed nodes.
