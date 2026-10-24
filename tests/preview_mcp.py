from flask import Flask
from orcview.mcp_public import create_public_app
import uvicorn
uvicorn.run(create_public_app(Flask(__name__)),host='127.0.0.1',port=9021,log_level='critical',access_log=False)
