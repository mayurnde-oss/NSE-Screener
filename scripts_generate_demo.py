"""Optional synthetic-data smoke test. These are NOT actual stock prices."""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
p=ROOT/'data/prices'
p.mkdir(parents=True,exist_ok=True)
ids=pd.bdate_range(end='2026-11-20',periods=2600)
for i,ticker in enumerate(('DEMO1.NS','DEMO2.NS')):
    rng=np.random.default_rng(100+i)
    drifts=rng.normal(loc=.0003,scale=.015,size=len(ids))
    close=100*(1+drifts).cumprod()
    high=close*(1+rng.uniform(.005,.015,len(ids)))
    low=close*(1-rng.uniform(.005,.015,len(ids)))
    frame=pd.DataFrame({'Date':ids.date,'Open':close,'High':high,'Low':low,
                        'Close':close,'Volume':1000000})
    frame.to_csv(p/f'{ticker}.csv',index=False)
(ROOT/'data/demo_universe.csv').write_text('Symbol,Company Name,Industry\nDEMO1,FAKE Synthetic Company 1,Test Industry\nDEMO2,FAKE Synthetic Company 2,Test Industry\n',encoding='utf-8')
print('Created 2 synthetic tickers. Not real prices. Do not treat demo output as trading signals.')
