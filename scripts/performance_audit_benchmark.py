"""Run from the repository root before committing: compare HEAD cache reader with working tree.

Controlled 200 ms provider delay; no live provider calls in the benchmark.
"""
import ast, statistics, subprocess, threading, time, sys, os
sys.path.insert(0, os.getcwd())
import app
source=subprocess.check_output(['git','show','HEAD:app.py'],text=True)
node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='get_cached_dashboard_data')
namespace=dict(vars(app))
exec(compile(ast.Module(body=[node],type_ignores=[]),'<baseline-dashboard-cache>','exec'),namespace)
baseline=namespace['get_cached_dashboard_data']
data=app.prepare_dashboard_data(include_market_snapshots=False,local_only=True)
before=[];after=[]
for trial in range(5):
    done=threading.Event()
    def slow(**kwargs):
        time.sleep(.2)
        done.set()
        return dict(data)
    namespace['prepare_dashboard_data']=slow
    namespace['DASHBOARD_CACHE']={'data':dict(data),'timestamp':1}
    started=time.perf_counter();baseline(include_market_snapshots=False);before.append((time.perf_counter()-started)*1000)
    app.prepare_dashboard_data=slow
    app.DASHBOARD_CACHE={'data':dict(data),'timestamp':1}
    app.DASHBOARD_REFRESH_PENDING=False
    app.DASHBOARD_REFRESH_RETRY_AT=0
    done.clear()
    started=time.perf_counter();app.get_cached_dashboard_data(include_market_snapshots=False,nonblocking=True);after.append((time.perf_counter()-started)*1000)
    done.wait(2)
    while app.DASHBOARD_REFRESH_PENDING: time.sleep(.001)
print({'provider_delay_ms':200,'trials':5,'before_median_ms':round(statistics.median(before),3),'after_median_ms':round(statistics.median(after),3),'before_samples_ms':before,'after_samples_ms':after})
