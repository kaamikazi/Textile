@echo off
cd /d "E:\al sadi"
"C:\Users\imran\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m streamlit run "E:\al sadi\app.py" --server.address 0.0.0.0 --server.port 8501 --server.headless true > "E:\al sadi\streamlit.log" 2>&1
