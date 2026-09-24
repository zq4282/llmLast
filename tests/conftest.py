"""测试环境显式使用内存会话存储，不依赖本机 Redis。"""

import os


os.environ["SESSION_STORE_BACKEND"] = "memory"
