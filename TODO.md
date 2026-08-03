
# 需求
1、现在知识库是系统入口，数据来源于文件上传，但文件从哪里来又是个问题，能在知识库前再增加一个菜单管理文件吗，文件能上传到这里，在知识库时可以上传也可以从文件中选，文件有层级目录，知识库上传的文件就默认进入一个目录。
2、同时文件管理 中的文件能通过系统.env中配置的大模型从互联网上爬取，输入关键字爬取信息
设计下以上功能，不仅限于以上，有更好的建议可以提出，出个设计方案，解决系统文件来源问题

1、增加了文件菜单，里面可输入关键字爬取新闻内容，爬取后的文件内容不对，里面还有模型思考内容，去掉
2、爬取内容时可选择低、中、高三档分析维度，越多分析的越细，内容也越多，默认是中档，现在的爬取逻辑是什么


## 文件管理
加一个全部折叠、打开文件夹图标

## 图谱
增加编辑图谱功能，可加节点、修改节点、删除节点、加关系、修改关系、删关系


# Bug
## 启动时不要连hg了
(MaxRetryError('HTTPSConnectionPool(host=\'huggingface.co\', port=443): Max retries exceeded with url: /BAAI/bge-small-zh-v1.5/resolve/main/config_sentence_transformers.json (Caused by NewConnectionError("HTTPSConnection(host=\'huggingface.co\', port=443): Failed to establish a new connection: [WinError 10061] 由于目标计算机积极拒绝，无法连接。"))'), '(Request ID: 9c670b1c-b225-4409-9156-0923eb5a7eed)')' thrown while requesting HEAD https://huggingface.co/BAAI/bge-small-zh-v1.5/resolve/main/config_sentence_transformers.json
2026-05-24 20:29:05 - huggingface_hub.utils._http - WARNING - '(MaxRetryError('HTTPSConnectionPool(host=\'huggingface.co\', port=443): Max retries exceeded with url: /BAAI/bge-small-zh-v1.5/resolve/main/config_sentence_transformers.json (Caused by NewConnectionError("HTTPSConnection(host=\'huggingface.co\', port=443): Failed to establish a new connection: [WinError 10061] 由于目标计算机积极拒绝，无法连接。"))'), '(Request ID: 9c670b1c-b225-4409-9156-0923eb5a7eed)')' thrown while requesting HEAD https://huggingface.co/BAAI/bge-small-zh-v1.5/resolve/main/config_sentence_transformers.json
Retrying in 2s [Retry 2/5].
2026-05-24 20:29:05 - huggingface_hub.utils._http - WARNING - Retrying in 2s [Retry 2/5].
2026-05-24 20:29:15 - __main__ - INFO - Loading LLM provider...
2026-05-24 20:29:15 - langchain_openai.chat_models._client_utils - INFO - langchain-openai detected system proxy configuration and no explicit `http_socket_options` / `http_client` / `http_async_client` / `openai_proxy`; skipping the custom `httpx` transport so httpx's env-proxy auto-detection applies. Pass `http_socket_options=[...]` to opt back into kernel-level TCP keepalive tuning on top of the env proxy.






