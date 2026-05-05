# Design Document

## For Conversational Search Node Package

- **Version:** 0.1
- **Author:** Claude
- **Date:** 2025-11-22
- **Status:** Approved

---

## Overview

The Conversational Search Node Package provides a comprehensive set of modular, graph-ready Orcheo nodes that enable builders to compose ingestion, retrieval, grounding, and answer generation workflows for conversational search applications. This package eliminates the need for bespoke glue code by standardizing on reusable components with shared interfaces.

The package addresses a critical gap where teams currently hand-roll conversational search flows, leading to duplicated work and inconsistent abstractions. By providing modular nodes covering the full pipeline from document ingestion through evaluation, this design enables rapid experimentation while supporting production operations with built-in guardrails.

Key goals include: (1) delivering plug-and-play nodes for ingestion, retrieval, and generation; (2) ensuring composability through typed interfaces and shared abstractions; and (3) providing guardrails (hallucination detection, policy compliance) that ease production operations.

## Components

### Data Ingestion Components

- **DocumentLoaderNode**
  - Connects to file, web, and API sources with format normalization
  - Outputs standardized document objects for downstream processing

- **ChunkingStrategyNode**
  - Applies configurable character/token chunking rules with overlap control
  - Interfaces with DocumentLoaderNode output and feeds ChunkEmbeddingNode

- **MetadataExtractorNode**
  - Attaches structured metadata (title, source, tags) to documents
  - Powers filtering and ranking in downstream retrieval nodes

- **ChunkEmbeddingNode**
  - Generates vector records per chunk using configurable embedding functions
  - Emits named embedding sets for downstream persistence or analytics
  - Ships helper factories in `orcheo.nodes.conversational_search.embeddings` for common providers (LangChain dense models plus Pinecone BM25/SPLADE sparse encoders) so demos can register embedding identifiers declaratively

- **VectorStoreUpsertNode**
  - Persists selected embedding sets into BaseVectorStore adapters (InMemory, Pinecone)
  - Returns indexed IDs, counts, and namespace metadata for observability

- **IncrementalIndexerNode**
  - Delta-sync pipeline detecting adds/updates/deletes
  - Reduces reindexing overhead for large corpora

### Retrieval Components

- **DenseSearchNode**
  - Dense similarity search using BaseVectorStore abstraction
  - Returns scored document chunks with metadata

- **SparseSearchNode**
  - Keyword-based sparse retrieval for deterministic matching
  - Optionally loads candidate chunks from a vector store (e.g., Pinecone) before computing BM25 for very large corpora
  - Complements dense search for hybrid strategies

- **HybridFusionNode**
  - Merges retriever outputs via RRF or weighted sum strategies
  - Interfaces with multiple retrieval nodes' outputs

- **WebSearchNode**
  - Live web search for freshness (Tavily integration)
  - Optional component for supplementing indexed content

- **ReRankerNode**
  - Cross-encoder or LLM scoring for top-k result refinement
  - Post-processes retrieval results before generation

- **SourceRouterNode**
  - Routes queries to appropriate sources via heuristics or learned models
  - Supports multiple backends (web search, knowledge graphs)

### Query Processing Components

- **QueryRewriteNode**
  - Uses conversation history to rewrite/expand user queries
  - Depends on conversation state for context

- **CoreferenceResolverNode**
  - Resolves pronouns and entity references using neural approaches
  - Critical for multi-turn conversation accuracy

- **QueryClassifierNode**
  - Routes queries by intent (search vs. clarification vs. finalization)
  - Powers conditional branching in workflow graphs

- **ContextCompressorNode**
  - Deduplicates retrieved context and enforces token budgets
  - Prepares optimized context for generation nodes

### Conversation Management Components

- **ConversationStateNode**
  - Maintains per-session context, participants, and runtime state
  - Central state management for conversation workflows

- **ConversationCompressorNode**
  - Summarizes long conversation histories within token budgets
  - Feeds compressed context to downstream nodes

- **TopicShiftDetectorNode**
  - AI-based detection of query divergence with configurable sensitivity
  - Enables adaptive conversation flow control

- **MemorySummarizerNode**
  - Writes episodic memory to BaseMemoryStore
  - Enables personalization across sessions

### Generation & Guardrail Components

- **GroundedGeneratorNode**
  - Core generative responder citing retrieved context
  - Primary answer generation node with grounding

- **StreamingGeneratorNode**
  - Streams responses via async iterators
  - Enables responsive UX for long generations

- **HallucinationGuardNode**
  - Validates responses using LLM Judge approach
  - Routes to fallback on detected hallucinations

- **CitationsFormatterNode**
  - Produces structured reference payloads (URL, title, snippet)
  - Post-processes generator output for citation display

- **QueryClarificationNode**
  - Requests user clarification when intent is ambiguous
  - Improves retrieval precision through dialog

### Memory & Optimization Components

- **AnswerCachingNode**
  - Caches semantically similar Q&A pairs with TTL policies
  - Reduces latency and compute for repeated queries

- **SessionManagementNode**
  - Controls session lifecycle, concurrency, and cleanup
  - Operational node for multi-workspace deployments

- **MultiHopPlannerNode**
  - Plans sequential retrieval hops for complex questions
  - Enables decomposition of multi-part queries

### Compliance Components

- **PolicyComplianceNode**
  - Enforces content filters (PII, toxicity) with audit logging
  - Configurable policy hooks for regional requirements

- **MemoryPrivacyNode**
  - Applies redaction and retention policies to stored state
  - Ensures compliance with data protection regulations

### Evaluation & Tooling Components

- **DatasetNode, RetrievalEvaluationNode, AnswerQualityEvaluationNode**
  - Manages golden datasets and computes metrics (Recall@k, MRR, NDCG, MAP)
  - Scores answers via LLM-as-a-judge or rule-based metrics

- **LLMJudgeNode, FailureAnalysisNode, ABTestingNode**
  - Runs LLM evaluators, categorizes failures, manages A/B experiments
  - Supports research workflows and continuous improvement

## Request Flows

### Flow 1: Basic RAG Pipeline

1. **DocumentLoaderNode** ingests documents from configured sources
2. **ChunkingStrategyNode** splits documents into chunks with overlap
3. **MetadataExtractorNode** attaches structured metadata
4. **ChunkEmbeddingNode** generates vector records for each chunk
5. **VectorStoreUpsertNode** persists selected vectors into the configured store
6. User submits query
7. **DenseSearchNode** performs dense similarity search
8. **GroundedGeneratorNode** generates response citing retrieved context
9. Response returned to user with citations

### Flow 2: Hybrid Search with Re-ranking

1. User submits query
2. **QueryRewriteNode** expands query using conversation context
3. **DenseSearchNode** performs dense retrieval (parallel)
4. **SparseSearchNode** performs sparse retrieval (parallel)
5. **HybridFusionNode** merges results via RRF strategy
6. **ReRankerNode** applies cross-encoder scoring to top-k
7. **ContextCompressorNode** deduplicates and enforces token budget
8. **GroundedGeneratorNode** generates grounded response
9. **CitationsFormatterNode** structures reference payloads
10. Response with structured citations returned to user

### Flow 3: Conversational Multi-turn Search

1. **ConversationStateNode** loads session context
2. User submits follow-up query
3. **TopicShiftDetectorNode** checks for topic divergence
4. **CoreferenceResolverNode** resolves pronouns/entities
5. **QueryRewriteNode** rewrites query with conversation context
6. **QueryClassifierNode** routes to appropriate path:
   - If search intent: proceed to retrieval
   - If clarification needed: route to **QueryClarificationNode**
7. **DenseSearchNode** retrieves relevant documents
8. **ContextCompressorNode** optimizes context
9. **GroundedGeneratorNode** generates response
10. **HallucinationGuardNode** validates response fidelity
11. **ConversationStateNode** updates session state
12. **MemorySummarizerNode** writes episodic memory
13. Response returned to user

### Flow 4: Multi-hop Reasoning

1. User submits complex query requiring decomposition
2. **MultiHopPlannerNode** creates retrieval plan with sequential hops
3. For each hop:
   - **QueryRewriteNode** generates sub-query
   - **DenseSearchNode** retrieves relevant context
   - Results accumulated for next hop
4. **ContextCompressorNode** consolidates all retrieved context
5. **GroundedGeneratorNode** synthesizes final response
6. Response with multi-source citations returned

### Flow 5: Evaluation Pipeline

1. **DatasetNode** loads golden dataset with relevance labels
2. Retrieval nodes execute on test queries
3. **RetrievalEvaluationNode** computes Recall@k, MRR, NDCG, MAP
4. **GroundedGeneratorNode** generates answers
5. **AnswerQualityEvaluationNode** scores faithfulness/relevance
6. **LLMJudgeNode** runs LLM evaluation
7. **FailureAnalysisNode** categorizes failure modes
8. Metrics and reports exported for analysis

## API Contracts

### Workflow-Based Operations

All conversational search operations are composed as Orcheo workflows rather than dedicated HTTP endpoints. This approach:
- Maintains consistency with Orcheo's workflow-first architecture
- Enables reusability and composition of index-building logic
- Leverages existing workflow observability, monitoring, and error handling
- Allows chaining with other nodes (e.g., fetch documents → build index → notify)

#### Index Building Workflow

Create a workflow that ingests documents and builds the vector index:

```yaml
# workflows/build-search-index.yaml
name: build-search-index
nodes:
  - id: load_docs
    type: document_loader
    config:
      source_type: file
      source_config:
        path: "{{inputs.source_path}}"
      format_handlers: ["pdf", "html", "txt", "docx"]

  - id: chunk
    type: chunking_strategy
    config:
      strategy: "token"
      chunk_size: 512
      overlap: 50

  - id: extract_metadata
    type: metadata_extractor
    config:
      extract_fields: ["title", "author", "date"]

  - id: chunk_embedding
    type: chunk_embedding
    config:
      embedding_functions:
        default: "{{inputs.embedding_model}}"

  - id: vector_upsert
    type: vector_store_upsert
    config:
      vector_store: "{{inputs.vector_store_id}}"

edges:
  - from: load_docs
    to: chunk
  - from: chunk
    to: extract_metadata
  - from: extract_metadata
    to: chunk_embedding
  - from: chunk_embedding
    to: vector_upsert
```

Trigger manually or via webhook:
```bash
orcheo workflow run build-search-index \
  --input source_path=/data/documents \
  --input vector_store_id=my-pinecone-index
```

#### Conversational Search Workflow

Reference the pre-built index in your search workflow:

```yaml
# workflows/conversational-search.yaml
name: conversational-search
nodes:
  - id: load_state
    type: conversation_state
    config:
      memory_store: "redis"
      session_ttl: 3600

  - id: rewrite
    type: query_rewrite

  - id: search
    type: dense_search
    config:
      vector_store: "{{inputs.vector_store_id}}"
      top_k: 10

  - id: generate
    type: grounded_generator
    config:
      citation_style: "inline"
      max_tokens: 1024

edges:
  - from: load_state
    to: rewrite
  - from: rewrite
    to: search
  - from: search
    to: generate
```

#### Evaluation Workflow

Run evaluations as a separate workflow:

```yaml
# workflows/evaluate-search.yaml
name: evaluate-search
nodes:
  - id: load_dataset
    type: dataset
    config:
      dataset_id: "{{inputs.dataset_id}}"

  - id: run_retrieval
    type: dense_search
    config:
      vector_store: "{{inputs.vector_store_id}}"

  - id: evaluate
    type: retrieval_evaluation
    config:
      metrics: ["recall_at_k", "mrr", "ndcg"]

edges:
  - from: load_dataset
    to: run_retrieval
  - from: run_retrieval
    to: evaluate
```

### Node Configuration Schema

All nodes follow a consistent configuration pattern:

```python
class NodeConfig(BaseModel):
    """Base configuration for all conversational search nodes."""
    name: str
    node_type: str
    enabled: bool = True
    retry_config: RetryConfig = RetryConfig()

class RetryConfig(BaseModel):
    max_retries: int = 3
    backoff_base: float = 2.0
    max_delay: float = 60.0


class CircuitBreakerConfig(BaseModel):
    failure_threshold: int = 5
    reset_timeout_seconds: int = 30
    half_open_max_calls: int = 2


class ErrorResponse(BaseModel):
    code: Literal[
        "VALIDATION_ERROR",
        "AUTH_REQUIRED",
        "FORBIDDEN",
        "NOT_FOUND",
        "UPSTREAM_ERROR",
        "RATE_LIMITED",
        "GENERATION_TIMEOUT",
    ]
    message: str
    details: dict | None = None
    retryable: bool = False
```

### DocumentLoaderNode

```python
class DocumentLoaderConfig(NodeConfig):
    node_type: Literal["document_loader"] = "document_loader"
    source_type: Literal["file", "web", "api"]
    source_config: dict  # Type-specific configuration
    format_handlers: list[str] = ["pdf", "html", "txt", "docx"]
    batch_size: int = 100

# Input State
{
    "sources": [{"type": "file", "path": "/data/docs"}]
}

# Output State
{
    "documents": [
        {
            "id": "doc_123",
            "content": "...",
            "metadata": {"source": "...", "format": "pdf"}
        }
    ]
}
```

### DenseSearchNode

```python
class DenseSearchConfig(NodeConfig):
    node_type: Literal["dense_search"] = "dense_search"
    vector_store: str  # Reference to configured store
    top_k: int = 10
    score_threshold: float = 0.0
    filter_metadata: dict = {}
    include_metadata: bool = True

# Input State
{
    "query": "What are the key features?",
    "filters": {"source": "documentation"}
}

# Output State
{
    "results": [
        {
            "id": "chunk_456",
            "content": "...",
            "score": 0.89,
            "metadata": {"source": "...", "page": 5}
        }
    ]
}
```

### GroundedGeneratorNode

```python
class GroundedGeneratorConfig(NodeConfig):
    node_type: Literal["grounded_generator"] = "grounded_generator"
    llm_config: LLMConfig
    system_prompt: str
    citation_style: Literal["inline", "footnote", "endnote"] = "inline"
    max_tokens: int = 1024
    temperature: float = 0.1
    circuit_breaker: CircuitBreakerConfig = CircuitBreakerConfig()

# Input State
{
    "query": "What are the key features?",
    "context": [{"id": "...", "content": "...", "metadata": {...}}],
    "conversation_history": [...]
}

# Output State
{
    "response": "The key features include... [1]",
    "citations": [
        {"id": "1", "source_id": "chunk_456", "snippet": "..."}
    ],
    "tokens_used": 523
}
```

### Error Handling & Fallback Behaviors

- Nodes return `ErrorResponse` payloads with appropriate error codes (validation, auth, not found, upstream failures, rate limited, generation timeout).
- Nodes share retry semantics via `RetryConfig`; non-retryable errors short-circuit the graph and bubble up immediately.
- Circuit breakers wrap outbound calls (vector store, LLMs, web search) with `failure_threshold=5` and `reset_timeout_seconds=30` defaults. Half-open probes use `half_open_max_calls=2` to re-test availability.
- Fallback routes:
  - `HallucinationGuardNode` -> return clarification prompt if hallucination detected.
  - `GroundedGeneratorNode` -> downgrade to non-streaming generation on streaming failure.
  - `HybridFusionNode` -> degrade to single retriever if one backend fails.
  - `SessionManagementNode` -> graceful session cleanup when token validation fails or TTL expiry hits.

### HybridFusionNode

```python
class HybridFusionConfig(NodeConfig):
    node_type: Literal["hybrid_fusion"] = "hybrid_fusion"
    strategy: Literal["rrf", "weighted_sum"] = "rrf"
    weights: dict[str, float] = {}  # For weighted_sum
    rrf_k: int = 60  # For RRF
    top_k: int = 10

# Input State
{
    "retrieval_results": {
        "vector": [{"id": "...", "score": 0.9, ...}],
        "bm25": [{"id": "...", "score": 15.2, ...}]
    }
}

# Output State
{
    "fused_results": [
        {"id": "...", "score": 0.85, "sources": ["vector", "bm25"]}
    ]
}
```

### ConversationStateNode

```python
class ConversationStateConfig(NodeConfig):
    node_type: Literal["conversation_state"] = "conversation_state"
    memory_store: str  # Reference to configured store
    max_turns: int = 50
    session_ttl: int = 3600  # seconds

# Input State
{
    "session_id": "sess_abc123",
    "user_message": "Tell me more about that"
}

# Output State
{
    "session_id": "sess_abc123",
    "conversation_history": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."}
    ],
    "metadata": {"turn_count": 5, "topic": "features"}
}
```

## Data Models / Schemas

### Document Schema

| Field | Type | Description |
|-------|------|-------------|
| id | string | Unique document identifier |
| content | string | Raw document content |
| metadata | object | Structured metadata (source, format, etc.) |
| chunks | array | Optional pre-chunked segments |
| embedding | array[float] | Optional pre-computed embedding |

### Chunk Schema

| Field | Type | Description |
|-------|------|-------------|
| id | string | Unique chunk identifier |
| document_id | string | Parent document reference |
| content | string | Chunk text content |
| metadata | object | Inherited + chunk-specific metadata |
| start_index | int | Character offset in source document |
| end_index | int | End character offset |

### Conversation Turn Schema

| Field | Type | Description |
|-------|------|-------------|
| id | string | Unique turn identifier |
| session_id | string | Parent session reference |
| role | enum | "user", "assistant", "system" |
| content | string | Message content |
| timestamp | datetime | Turn creation time |
| metadata | object | Intent, entities, etc. |

### Retrieval Result Schema

```json
{
  "id": "string",
  "content": "string",
  "score": 0.0,
  "metadata": {
    "source": "string",
    "page": 0,
    "section": "string"
  },
  "retriever": "string"
}
```

### Evaluation Result Schema

```json
{
  "query_id": "string",
  "metrics": {
    "recall_at_k": {"5": 0.8, "10": 0.9},
    "mrr": 0.75,
    "ndcg": 0.82,
    "map": 0.78
  },
  "answer_quality": {
    "faithfulness": 0.9,
    "relevance": 0.85,
    "completeness": 0.8
  }
}
```

### Base Abstractions

```python
class BaseVectorStore(ABC):
    """Abstract interface for vector store implementations."""

    @abstractmethod
    async def add(self, vectors: list[Vector]) -> list[str]: ...

    @abstractmethod
    async def search(
        self,
        query_vector: list[float],
        top_k: int,
        filters: dict
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def delete(self, ids: list[str]) -> int: ...

class BaseMemoryStore(ABC):
    """Abstract interface for conversation memory storage."""

    @abstractmethod
    async def get_session(self, session_id: str) -> Session: ...

    @abstractmethod
    async def save_turn(self, session_id: str, turn: Turn) -> None: ...

    @abstractmethod
    async def get_history(
        self,
        session_id: str,
        limit: int
    ) -> list[Turn]: ...

    @abstractmethod
    async def cleanup(self, session_id: str) -> None:
        """Remove session state, cached summaries, and associated secrets."""
```

## Security Considerations

### Authentication & Authorization
- Nodes inherit Orcheo's authentication context
- Vector store credentials managed via Orcheo secret bindings
- Session tokens validated on each conversation turn
- Role-based access control for sensitive operations (memory deletion, policy override)
- JWT validation uses issuer/audience checks, `kid`-pinned JWKS, and 5-minute clock-skew tolerance
- Session tokens carry workspace + role claims; `SessionManagementNode` enforces TTL and idle timeouts

### Data Privacy & Redaction
- **MemoryPrivacyNode** applies configurable redaction patterns (PII, credentials)
- Conversation histories support field-level encryption
- Retention policies enforce automatic deletion after TTL
- Audit logging for all data access and modifications
- PII detection leverages pattern library: emails (`[\w.+-]+@[\w-]+\.[\w.-]+`), phone numbers (`\+?[0-9 .()-]{7,}`), SSN (`\b\d{3}-\d{2}-\d{4}\b`), API keys (`sk-[A-Za-z0-9]{32,}`)
- Redaction strategies configurable as mask (`****`), hash, or drop; defaults to mask before persistence

### Input Validation & Sanitization
- All node configs validated via Pydantic schemas
- Query inputs sanitized to prevent injection attacks
- Document content scanned for malicious payloads before indexing
- Token limits enforced to prevent resource exhaustion
- Configuration validation examples: enforce `top_k <= 50`, reject empty `source_config`, require TLS for vector store endpoints

### Secrets Management
- API keys and credentials never logged or stored in state
- Vector store connection strings use Orcheo secret references
- LLM API keys managed through environment bindings
- Sensitive metadata redacted in error messages

### Content Safety
- **PolicyComplianceNode** filters PII, toxicity, and prohibited content
- Configurable blocklists and allowlists per deployment
- Content moderation hooks for custom policies
- Audit trail for compliance verification
- Guardrail fallback: when blocked, return redacted rationale and route to `QueryClarificationNode` for safe re-prompt

## Performance Considerations

### Target Metrics
- **Ingestion throughput**: >= 500 documents/minute
- **Retrieval p95 latency**: <= 1.5 seconds
- **Generation p95 latency**: <= 4 seconds (GPU-backed LLM)

### Caching Strategy
- **AnswerCachingNode** caches semantically similar Q&A pairs
- Embedding cache for frequently queried documents
- Session state cached in-memory with Redis backing
- TTL-based invalidation with configurable policies

### Batching & Parallelization
- Document ingestion batched (default: 100 docs)
- Embedding generation parallelized across batch
- Multiple retrievers execute in parallel before fusion
- Async streaming for generation responses

### Resource Management
- Token budgets enforced via **ContextCompressorNode**
- Connection pooling for vector store clients
- Circuit breakers for external service calls
- Graceful degradation when resources constrained

### Pagination & Lazy Loading
- Large result sets paginated with cursor-based navigation
- Conversation histories lazy-loaded on demand
- Incremental indexing for large corpus updates

## Testing Strategy

### Unit Tests
- Each node tested in isolation with mocked dependencies
- Config validation tests for all node types
- Edge cases: empty inputs, malformed data, boundary conditions
- Located under `tests/nodes/conversational_search/`

### Integration Tests
- End-to-end pipeline tests for reference graphs
- Vector store adapter integration (Pinecone, PGVector)
- LLM provider integration with rate limiting
- Session management lifecycle tests
- Failure mode coverage: vector store timeouts, LLM 429/5xx retries, missing citations, guardrail blocks

### Performance Tests
- Latency benchmarks against target metrics
- Throughput tests for ingestion pipelines
- Load tests for concurrent session handling
- Memory profiling for long-running sessions
- Concurrency goals: sustain 200 concurrent conversations with p95 < 4s; soak test 1h with no memory leak >5%
- Memory usage estimates: ~30 MB baseline per worker + 1.5 KB per conversation turn retained in Redis-backed memory store

### Evaluation Tests
- Golden dataset tests with expected metric ranges
- Regression tests using LLMJudgeNode scores
- A/B test infrastructure validation
- Citation accuracy verification

### Manual QA Checklist
- [ ] Basic RAG pipeline produces grounded responses
- [ ] Hybrid search improves recall over single retriever
- [ ] Coreference resolution handles pronouns correctly
- [ ] Topic shift detection triggers appropriately
- [ ] Hallucination guard catches fabricated content
- [ ] Citations link to correct source documents
- [ ] Session state persists across turns
- [ ] Streaming generation displays progressively
- [ ] Workflow errors return structured error responses
- [ ] Streaming reconnects resume without duplicate tokens
- [ ] Guardrail blocks present sanitized rationale and fallback prompt

## Rollout Plan

### Phase 1: MVP (Research Pods)
**Target**: Enable experimentation with basic conversational search

**Nodes Delivered**:
- DocumentLoaderNode, ChunkingStrategyNode, MetadataExtractorNode, ChunkEmbeddingNode, VectorStoreUpsertNode
- DenseSearchNode, SparseSearchNode, HybridFusionNode, WebSearchNode
- QueryRewriteNode, CoreferenceResolverNode, QueryClassifierNode, ContextCompressorNode
- GroundedGeneratorNode

**Feature Flags**: `conversational_search_phase1`

**Success Criteria**:
- Retrieval p95 latency <= 1.5s
- Basic RAG pipeline functional
- Integration tests passing

### Phase 2: Conversational Features (Early Adopters)
**Target**: Multi-turn conversation quality

**Nodes Delivered**:
- ConversationStateNode, ConversationCompressorNode, TopicShiftDetectorNode
- MemorySummarizerNode
- QueryClarificationNode

**Migration**: Session state schema v1 -> v2

**Feature Flags**: `conversational_search_phase2`

**Success Criteria**:
- Multi-turn accuracy improvement >= 15%
- Session persistence functional

### Phase 3: Production Quality (Production Teams)
**Target**: Production readiness with guardrails

**Nodes Delivered**:
- IncrementalIndexerNode, ReRankerNode, SourceRouterNode
- StreamingGeneratorNode, HallucinationGuardNode, CitationsFormatterNode
- AnswerCachingNode, SessionManagementNode, MultiHopPlannerNode

**Feature Flags**: `conversational_search_phase3`

**Success Criteria**:
- Generation p95 latency <= 4s
- Hallucination rate < 5%
- Cache hit rate >= 30% for repeated queries

### Phase 4: Research & Operations (Broad Rollout)
**Target**: Continuous improvement and compliance

**Nodes Delivered**:
- PolicyComplianceNode, MemoryPrivacyNode
- DatasetNode, RetrievalEvaluationNode, AnswerQualityEvaluationNode
- LLMJudgeNode, FailureAnalysisNode, ABTestingNode
- TurnAnnotationNode, DataAugmentationNode
- UserFeedbackCollectionNode, FeedbackIngestionNode, AnalyticsExportNode

**Feature Flags**: `conversational_search_phase4`

**Success Criteria**:
- Evaluation infrastructure operational
- Compliance audits passing
- Feedback loop established

## Open Issues

- [ ] **Vector Store Prioritization**: Need early adopter survey to determine adapter priority (Pinecone vs. PGVector vs. LanceDB)
- [ ] **Compliance Review**: Legal/compliance review pending for MemoryPrivacyNode and PolicyComplianceNode regional policies
- [ ] **Streaming Protocol**: Evaluate SSE vs WebSocket for StreamingGeneratorNode output
- [ ] **Memory Store Backend**: Redis vs. PostgreSQL for BaseMemoryStore default implementation
- [ ] **Embedding Model Selection**: Default embedding model selection (OpenAI vs. open-source options)
- [ ] **Multi-hop Complexity Limits**: Maximum hop count and total token budget for MultiHopPlannerNode

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2025-11-22 | Claude | Initial draft |
