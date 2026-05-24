#!/usr/bin/env python3
"""
GraphRAG系统启动脚本
"""

import os
import sys
import argparse
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


def main():
    parser = argparse.ArgumentParser(description='计算机网络知识图谱GraphRAG系统')
    parser.add_argument('--mode', choices=['web', 'cli', 'test'], default='web',
                      help='运行模式: web(网页界面), cli(命令行), test(测试)')
    parser.add_argument('--host', default='0.0.0.0', help='Web服务器主机地址')
    parser.add_argument('--port', type=int, default=5001, help='Web服务器端口')
    parser.add_argument('--debug', action='store_true', help='启用调试模式')

    args = parser.parse_args()

    if args.mode == 'web':
        print("启动GraphRAG Web界面 (FastAPI + LangGraph + 记忆)...")
        from web.graph_rag_web import main

        print(f"访问地址: http://localhost:{args.port}")
        print(f"API 文档: http://localhost:{args.port}/docs")
        print("按 Ctrl+C 停止服务器")

        os.environ['WEB_HOST'] = args.host
        os.environ['WEB_PORT'] = str(args.port)
        os.environ['DEBUG'] = str(args.debug).lower()

        try:
            main()
        except KeyboardInterrupt:
            print("\n服务器已停止")

    elif args.mode == 'cli':
        print("启动GraphRAG命令行界面 (LangGraph)...")
        from src.langchain_config import LangGraphRAGConfig
        from src.graphrag_agent import GraphRAGAgent

        config = LangGraphRAGConfig()
        engine = GraphRAGAgent(config)

        print("GraphRAG CLI (输入 'quit' 退出)")
        session_id = None

        while True:
            try:
                question = input("\n问题: ").strip()
                if not question:
                    continue
                if question.lower() in ('quit', 'exit', 'q'):
                    break
                if question == 'new':
                    session_id = None
                    print("--- 新会话 ---")
                    continue

                result = engine.query(question, session_id=session_id)
                session_id = result.get("session_id")
                print(f"\n答案: {result['answer']}")
                print(f"[耗时: {result['processing_time']:.2f}s]")

            except KeyboardInterrupt:
                break

        engine.close()
        print("\n再见!")

    elif args.mode == 'test':
        print("运行GraphRAG系统测试...")
        from src.langchain_config import LangGraphRAGConfig
        from src.graphrag_agent import GraphRAGAgent

        config = LangGraphRAGConfig()
        engine = GraphRAGAgent(config)

        try:
            stats = engine.get_stats()
            print("知识图谱统计:")
            for key, value in stats.items():
                print(f"  - {key}: {value}")

            test_questions = [
                "TCP协议的工作原理是什么？",
                "网络层有哪些重要协议？",
            ]

            session_id = None
            for q in test_questions:
                print(f"\n{'='*60}")
                print(f"问题: {q}")
                result = engine.query(q, session_id=session_id)
                session_id = result.get("session_id")
                print(f"答案: {result['answer']}")
                print(f"耗时: {result['processing_time']:.2f}s")

            # 测试记忆：追问
            print(f"\n{'='*60}")
            follow_up = "能详细解释一下吗？"
            print(f"追问: {follow_up}")
            result = engine.query(follow_up, session_id=session_id)
            print(f"答案: {result['answer']}")
            print(f"(使用了同一会话ID: {session_id})")

        finally:
            engine.close()


if __name__ == '__main__':
    main()
