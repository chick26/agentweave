import asyncio
import os
from openai import AsyncOpenAI
from pathlib import Path
from agent_runtime.common import load_local_env_files

async def test_call(enable_thinking: bool):
    root = Path(".").resolve()
    load_local_env_files(root)
    
    base_url = os.getenv("QWEN36_BASE_URL")
    model_name = os.getenv("QWEN36_MODEL")
    api_key = os.getenv("QWEN36_API_KEY", "not-needed")
    
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    
    system_prompt = """你是 Text2SQL Subagent，一个一次性的专用数据问答执行单元。Orchestrator 会交给你一个明确的数据查询任务，你负责先规划只读 SQL、再执行查询、返回结构化结果。你不继承主对话上下文，不和用户闲聊，执行完一个任务就结束。

# 输出格式

<output_json_schema>
严格输出以下 JSON，不要输出其他内容：
{
  "answer": "中文简洁回答",
  "subagent": "text2sql",
  "trace": [],
  "error": "",
  "extras": {
    "domain": "激活的 domain 名称",
    "sql": "最终执行的 SQL",
    "result_id": "execute_sql 返回的 result_id",
    "row_count": 0,
    "truncated": false,
    "rows": []
  }
}
</output_json_schema>
"""
    
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_domain_schema",
                "description": "Load the real database schema for one Text2SQL domain. Always call this before generating SQL.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "domain_name": {"type": "string"}
                    },
                    "required": ["domain_name"]
                }
            }
        }
    ]
    
    extra_body = {}
    if not enable_thinking:
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
        
    print(f"\n--- Testing with enable_thinking={enable_thinking} ---")
    try:
        response = await client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "403机房有多少可用机柜？"}
            ],
            tools=tools,
            tool_choice="auto",
            extra_body=extra_body if extra_body else None
        )
        message = response.choices[0].message
        print("Response Content:", repr(message.content))
        print("Response Tool Calls:", message.tool_calls)
    except Exception as e:
        print("Error:", e)

async def main():
    await test_call(enable_thinking=False)
    await test_call(enable_thinking=True)

if __name__ == "__main__":
    asyncio.run(main())
