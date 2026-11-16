"""Public builds expose an explicit optional-connector status, not third-party code."""
from flask import Flask,jsonify
from waitress import serve
app=Flask(__name__)
@app.get('/health')
def health():return jsonify(status='ok',component='external-connector',configured=False)
@app.route('/api/<path:rest>',methods=['GET','POST','PUT','DELETE'])
def unavailable(rest):
    return jsonify(detail='公开版采集功能需要外部组件。请按 GitHub README 的外部采集接入说明配置。',message='外部采集组件尚未接入'),503
if __name__=='__main__':serve(app,host='127.0.0.1',port=8021,threads=4)
