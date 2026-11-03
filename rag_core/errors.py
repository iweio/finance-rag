"""RagEngine 的对外异常。

P1 内部所有异常（Milvus 连接失败、LLM 超时、模型加载失败）都必须包成 RagError 抛出，
不得让 pymilvus / openai 的原生异常穿过边界——否则 P3 的 except 写不准，且堆栈会漏到前端。

P3 侧转换（API.md §0.4）：
  RETRIEVE_FAILED  → HTTP 500, code 50001
  GENERATE_FAILED  → HTTP 500, code 50002
  NOT_FOUND        → HTTP 404, code 40401
  其它             → HTTP 500, code 50001
"""


class RagError(Exception):
    """RAG 核心的唯一对外异常。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")
