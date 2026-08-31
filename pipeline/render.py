#!/usr/bin/env python3
"""Render dashboard.html from the snapshot, with the data INLINED.

Inlined, not fetch()ed, because the dashboard is meant to be opened straight
off disk and a file:// page cannot fetch a sibling JSON under CORS. That is
the whole reason this is a separate step from collect.py.

    python3 pipeline/render.py
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chain as C

CSS = """
:root{--paper:#EFF2F1;--card:#F7F9F8;--ink:#12201F;--muted:#5C6E6C;--rule:#D2DBD8;
 --accent:#0B6E5F;--accent-soft:#DCEAE6;--amber:#8A5209;--amber-soft:#F3E6D2;
 --rose:#9C3348;--rose-soft:#F3DCE0;--up:#0B6E5F;--down:#9C3348;--chip:#E4EBE9}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
 --paper:#0C1514;--card:#141F1E;--ink:#E4EDEA;--muted:#8CA39F;--rule:#243331;
 --accent:#4FD1BA;--accent-soft:#152B27;--amber:#D9A05B;--amber-soft:#2B2113;
 --rose:#E58098;--rose-soft:#2E171C;--up:#4FD1BA;--down:#E58098;--chip:#1D2A28}}
:root[data-theme="dark"]{--paper:#0C1514;--card:#141F1E;--ink:#E4EDEA;--muted:#8CA39F;
 --rule:#243331;--accent:#4FD1BA;--accent-soft:#152B27;--amber:#D9A05B;--amber-soft:#2B2113;
 --rose:#E58098;--rose-soft:#2E171C;--up:#4FD1BA;--down:#E58098;--chip:#1D2A28}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-size:15px;line-height:1.5;
 font-family:"IBM Plex Sans",system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
 -webkit-font-smoothing:antialiased}
.wrap{max-width:1580px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:1.5rem;margin:0 0 4px;letter-spacing:-.02em}
.sub{color:var(--muted);font-size:.83rem;margin-bottom:18px}
.mono{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace}
.bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
input[type=search],input[type=date]{background:var(--card);border:1px solid var(--rule);
 color:var(--ink);border-radius:8px;padding:8px 11px;font:inherit;font-size:.86rem}
input[type=search]{min-width:340px}
.tabs{display:flex;gap:6px}
button{background:var(--card);border:1px solid var(--rule);color:var(--ink);
 border-radius:8px;padding:7px 13px;font:inherit;font-size:.84rem;cursor:pointer}
button.on{background:var(--accent);border-color:var(--accent);color:var(--paper);font-weight:600}
.tf button{padding:6px 10px;font-size:.8rem}
label.chk{display:flex;align-items:center;gap:6px;font-size:.82rem;color:var(--muted);cursor:pointer}
table{border-collapse:collapse;width:100%;font-size:.84rem}
.scroll{overflow-x:auto;border:1px solid var(--rule);border-radius:10px;background:var(--card)}
th,td{padding:7px 10px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--rule)}
th:first-child,td:first-child,th.l,td.l{text-align:left}
th{position:sticky;top:0;background:var(--card);cursor:pointer;font-weight:600;
 font-size:.76rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);z-index:2}
th:hover{color:var(--ink)}
th.act{color:var(--accent)}
tbody tr:hover{background:var(--accent-soft)}
td.num{font-family:"IBM Plex Mono",ui-monospace,monospace}
.up{color:var(--up)}.down{color:var(--down)}.dim{color:var(--muted)}
.tag{display:inline-block;padding:1px 7px;border-radius:999px;font-size:.7rem;
 font-weight:600;letter-spacing:.03em}
.tag.reg{background:var(--accent-soft);color:var(--accent)}
.tag.unv{background:var(--rose-soft);color:var(--rose)}
.tag.warn{background:var(--amber-soft);color:var(--amber)}
.addr{color:var(--muted);cursor:pointer;font-size:.78rem}
.addr:hover{color:var(--accent)}
.prov{background:var(--card);border:1px solid var(--rule);border-radius:10px;
 padding:11px 14px;font-size:.79rem;color:var(--muted);margin-bottom:16px}
.prov b{color:var(--ink)}
.hide{display:none}
.meta{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.qcard{background:var(--card);border:1px solid var(--rule);border-radius:9px;
 padding:7px 11px;min-width:104px}
.qcard b{display:block;font-size:.95rem}
.qcard span{font-size:.72rem;color:var(--muted)}
.qcard.hot{border-color:var(--accent);background:var(--accent-soft)}
.grp{background:var(--chip)}
.grp td{font-weight:600;font-size:.8rem}
.note{font-size:.78rem;color:var(--muted);margin-top:10px}
a{color:var(--accent)}
"""

JS = r"""
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const D=JSON.parse(document.getElementById('data').textContent);
// Both tabs default to OLDEST FIRST: the point of the launch-date column is
// finding the first stock listed and the oldest surviving stock-paired pool.
// Memes is the default view: the stock tokens are a slow-moving registry,
// but which memecoins are being launched against them IS the live meta.
let TAB='memes', TF='h24',
    sortKey={memes:'swaps', stocks:'deployed_ts', pairs:'created_ts'},
    sortDir={memes:-1, stocks:1, pairs:1}, query='';

const TFS=[['m5','5m'],['m15','15m'],['h1','1h'],['h4','4h'],['h24','24h']];
const esc=v=>String(v==null?'':v).replace(/[&<>"']/g,c=>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtUsd=v=>v==null?'—':(v>=1e9?'$'+(v/1e9).toFixed(2)+'B':v>=1e6?'$'+(v/1e6).toFixed(2)+'M':
  v>=1e3?'$'+(v/1e3).toFixed(1)+'k':'$'+v.toFixed(v<1?4:2));
const fmtPx=v=>v==null?'—':v>=1?'$'+v.toFixed(2):'$'+v.toPrecision(3);
const fmtN=v=>v==null?'—':v.toLocaleString();
function pct(v){if(v==null)return '<span class="dim">—</span>';
  const c=v>0?'up':v<0?'down':'dim';const s=v>0?'+':'';
  return `<span class="${c}">${s}${Math.abs(v)>=100?v.toFixed(0):v.toFixed(2)}%</span>`;}
function age(ts){if(!ts)return '—';let s=Date.now()/1000-ts;
  if(s<3600)return Math.floor(s/60)+'m'; if(s<86400)return (s/3600).toFixed(1)+'h';
  return (s/86400).toFixed(1)+'d';}
function short(a){return a?a.slice(0,6)+'…'+a.slice(-4):'—';}
function copy(a){navigator.clipboard&&navigator.clipboard.writeText(a);}

function sorter(rows,key,dir){
  return rows.slice().sort((a,b)=>{
    let x=key.startsWith('chg.')?(a.chg||{})[key.slice(4)]:a[key];
    let y=key.startsWith('chg.')?(b.chg||{})[key.slice(4)]:b[key];
    if(x==null&&y==null)return 0; if(x==null)return 1; if(y==null)return -1;
    if(typeof x==='string')return dir*x.localeCompare(y);
    return dir*(x-y);});
}

function tfCols(row){return TFS.map(([k,l])=>
  `<td class="num${k===TF?' act':''}">${pct((row.chg||{})[k])}</td>`).join('');}

function renderStocks(){
  let rows=D.stocks;
  const from=$('#from').value,to=$('#to').value;
  if(from)rows=rows.filter(r=>r.deployed&&r.deployed>=from);
  if(to)rows=rows.filter(r=>r.deployed&&r.deployed<=to+'T23:59:59Z');
  if($('#nomkt').checked)rows=rows.filter(r=>r.pools>0);
  rows=sorter(rows,sortKey.stocks,sortDir.stocks);
  $('#count').textContent=rows.length+' of '+D.stocks.length+' stock tokens';
  return rows.map(r=>`<tr>
    <td class="l"><b>${esc(r.symbol)}</b> <span class="tag reg">REGISTRY</span></td>
    <td class="l">${esc(r.name.replace(' • Robinhood Token',''))}</td>
    <td class="l mono">${(r.deployed||'').slice(0,10)}</td>
    <td class="num dim">${age(r.deployed_ts)}</td>
    <td class="num">${fmtPx(r.price_usd)}</td>
    <td class="num">${fmtUsd(r.market_cap_usd)}</td>
    <td class="num">${fmtUsd(r.volume_24h)}</td>
    <td class="num">${fmtUsd(r.reserve_usd)}</td>
    <td class="num">${fmtN(r.pools)}</td>
    ${tfCols(r)}
    <td class="l addr mono" onclick="copy('${r.address}')" title="${r.address}">${short(r.address)}</td>
  </tr>`).join('');
}

function renderMemes(){
  let rows=D.pairs.filter(r=>r.meme);
  if(!$('#mdead').checked)rows=rows.filter(r=>r.indexed||r.swaps);
  const q2=$('#mquote').value.trim().toUpperCase();
  if(q2)rows=rows.filter(r=>(r.stock_symbol||'').toUpperCase()===q2);
  rows=sorter(rows,sortKey.memes,sortDir.memes);
  $('#count').textContent=rows.length+' meme pools shown · '
    +D.provenance.meme_pools_live.toLocaleString()+' live in window · '
    +D.provenance.meme_pools_total.toLocaleString()+' ever created';
  return rows.map(r=>`<tr>
    <td class="l"><b>${esc(r.symbol)}</b> <span class="dim">${esc((r.name||'').slice(0,26))}</span></td>
    <td class="l"><span class="tag warn">${esc(r.stock_symbol||'?')}</span></td>
    <td class="num">${fmtN(r.swaps)}</td>
    <td class="l mono">${(r.created||'').slice(0,16).replace('T',' ')}</td>
    <td class="num dim">${age(r.created_ts)}</td>
    <td class="num">${fmtUsd(r.liquidity_usd)}</td>
    <td class="num">${fmtUsd(r.volume_24h)}</td>
    ${tfCols(r)}
    <td class="l"><a href="https://dexscreener.com/robinhood/${r.pool}" target="_blank"
      rel="noopener" class="mono addr">${short(r.pool)}</a></td>
  </tr>`).join('');
}

function renderPairs(){
  let rows=D.pairs;
  if(!$('#dead').checked)rows=rows.filter(r=>r.indexed||r.swaps);
  rows=sorter(rows,sortKey.pairs,sortDir.pairs);
  $('#count').textContent=rows.length+' stock-quoted pools shown · '
    +D.provenance.pools_total.toLocaleString()+' exist on chain';
  return rows.map(r=>`<tr>
    <td class="l"><b>${esc(r.symbol)}</b>${r.registry_other?' <span class="tag reg">REGISTRY</span>':''}</td>
    <td class="l"><span class="tag warn">${esc(r.stock_symbol||'?')}</span></td>
    <td class="l dim">${esc(r.dex||r.kind||'')}</td>
    <td class="l mono">${(r.created||'').slice(0,16).replace('T',' ')}</td>
    <td class="num dim">${age(r.created_ts)}</td>
    <td class="num">${fmtUsd(r.liquidity_usd)}</td>
    <td class="num">${fmtUsd(r.volume_24h)}</td>
    <td class="num">${fmtN(r.swaps)}</td>
    ${tfCols(r)}
    <td class="l"><a href="https://dexscreener.com/robinhood/${r.pool}" target="_blank"
      rel="noopener" class="mono addr">${short(r.pool)}</a></td>
  </tr>`).join('');
}

function renderSearch(){
  const q=query.trim().toLowerCase();
  const hits=D.tokenList.filter(t=>
    t[1].toLowerCase()===q||t[1].toLowerCase().includes(q)||
    t[2].toLowerCase().includes(q)||t[0].includes(q));
  const groups={};
  hits.forEach(t=>{const g=(t[1]||'(no symbol)').toUpperCase();(groups[g]=groups[g]||[]).push(t);});
  const keys=Object.keys(groups).sort((a,b)=>
    (a.toLowerCase()===q?-1:b.toLowerCase()===q?1:groups[b].length-groups[a].length));
  if(!keys.length)return '<tr><td class="l" colspan="7">No token matches.</td></tr>';
  let html='';
  for(const k of keys.slice(0,40)){
    const g=groups[k].slice().sort((a,b)=>(a[4]||9e15)-(b[4]||9e15));
    const real=g.filter(t=>t[3]).length;
    const dep={};g.forEach(t=>{if(t[5])dep[t[5]]=(dep[t[5]]||0)+1;});
    html+=`<tr class="grp"><td class="l" colspan="7">${esc(k)} — ${g.length} token${g.length>1?'s':''}
      claim this ticker${real?` · ${real} issued by the stock registry`:' · none in the registry'}
      ${g.length>1?'· oldest first':''}</td></tr>`;
    html+=g.map(t=>{
      const farm=t[5]&&dep[t[5]]>1;
      return `<tr>
      <td class="l"><b>${esc(t[1])}</b></td>
      <td class="l">${esc((t[2]||'').slice(0,52))}</td>
      <td class="l">${t[3]?'<span class="tag reg">REGISTRY</span>':'<span class="tag unv">UNVERIFIED</span>'}</td>
      <td class="l mono">${t[4]?new Date(t[4]*1000).toISOString().slice(0,10):'—'}</td>
      <td class="num dim">${age(t[4])}</td>
      <td class="l mono addr" onclick="copy('${t[5]||''}')">${t[5]?short(t[5]):'—'}
        ${farm?`<span class="tag warn">farm ×${dep[t[5]]}</span>`:''}</td>
      <td class="l addr mono" onclick="copy('${t[0]}')" title="${t[0]}">${short(t[0])}</td>
    </tr>`;}).join('');
  }
  return html;
}

const HEADS={
 memes:[['symbol','Meme'],['stock_symbol','Quote stock'],['swaps','Swaps'],
   ['created_ts','Pool created'],['created_ts','Age'],['liquidity_usd','Liquidity'],
   ['volume_24h','Vol 24h'],...TFS.map(([k,l])=>['chg.'+k,l]),['pool','Pool']],
 stocks:[['symbol','Ticker'],['name','Name'],['deployed_ts','Launched'],['deployed_ts','Age'],
   ['price_usd','Price'],['market_cap_usd','Mkt cap'],['volume_24h','Vol 24h'],
   ['reserve_usd','Liquidity'],['pools','Pools'],...TFS.map(([k,l])=>['chg.'+k,l]),['address','Address']],
 pairs:[['symbol','Token'],['stock_symbol','Quote'],['dex','Venue'],['created_ts','Pool created'],
   ['created_ts','Age'],['liquidity_usd','Liquidity'],['volume_24h','Vol 24h'],['swaps','Swaps'],
   ...TFS.map(([k,l])=>['chg.'+k,l]),['pool','Pool']],
 search:[['','Ticker'],['','Name'],['','Verdict'],['','First seen'],['','Age'],
   ['','Deployer'],['','Address']]};

function draw(){
  const mode=query.trim()?'search':TAB;
  $('#thead').innerHTML='<tr>'+HEADS[mode].map(([k,l],i)=>
    `<th class="${i<2||i>=HEADS[mode].length-1?'l':''}${k===sortKey[mode]?' act':''}"
      ${k&&mode!=='search'?`onclick="setSort('${k}')"`:''}>${l}</th>`).join('')+'</tr>';
  $('#tbody').innerHTML=mode==='search'?renderSearch():mode==='memes'?renderMemes():mode==='stocks'?renderStocks():renderPairs();
  $('#filters').className=(mode==='stocks')?'bar':'bar hide';
  $('#pairfilters').className=(mode==='pairs')?'bar':'bar hide';
  $('#memefilters').className=(mode==='memes')?'bar':'bar hide';
  $('#metastrip').className=(mode==='memes')?'meta':'meta hide';
  if(mode==='search')$('#count').textContent='searching '+D.tokenList.length.toLocaleString()
    +' tokens ever paired with a stock';
  $$('.tabs button').forEach(b=>b.classList.toggle('on',b.dataset.tab===TAB&&mode!=='search'));
  $$('.tf button').forEach(b=>b.classList.toggle('on',b.dataset.tf===TF));
}
function setSort(k){const m=query.trim()?'search':TAB;
  if(sortKey[m]===k)sortDir[m]*=-1;else{sortKey[m]=k;sortDir[m]=-1;}draw();}
window.setSort=setSort;window.copy=copy;
$$('.tabs button').forEach(b=>b.onclick=()=>{TAB=b.dataset.tab;$('#q').value='';query='';draw();});
$$('.tf button').forEach(b=>b.onclick=()=>{TF=b.dataset.tf;
  const m=query.trim()?'search':TAB; if(m!=='search'){sortKey[m]='chg.'+TF;sortDir[m]=-1;}draw();});
$('#q').oninput=e=>{query=e.target.value;draw();};
['from','to','nomkt','dead','mdead','mquote'].forEach(id=>$('#'+id).oninput=draw);
draw();
"""


def main():
    snap = C.load("snapshot.json")
    if not snap:
        sys.exit("run pipeline/collect.py first")

    toks = snap.get("tokens") or {}
    # compact rows for the search index: [addr, symbol, name, registry, ts, deployer]
    tlist = [[t["address"], t.get("symbol") or "", (t.get("name") or "")[:64],
              1 if t.get("registry") else 0, t.get("deployed_ts"), t.get("deployer")]
             for t in toks.values() if (t.get("symbol") or t.get("name"))]
    payload = {k: snap[k] for k in ("generated", "window", "provenance", "stocks", "pairs")}
    payload["tokenList"] = tlist

    payload_json = json.dumps(payload).replace("<", "\\u003c")
    qm = snap.get("quote_meta") or []
    meta_html = "".join(
        f'<div class="qcard{" hot" if i < 3 else ""}"><b>{q["symbol"]}</b>'
        f'<span>{q["swaps"]:,} swaps · {q["pools"]} pools</span></div>'
        for i, q in enumerate(qm[:14]))
    if meta_html:
        meta_html = ('<div class="qcard"><b>Quote meta</b><span>stocks memes are '
                     'launching against, by 6h swaps</span></div>') + meta_html
    p = snap["provenance"]
    prov = (f'<b>{p["stocks_total"]}</b> stock tokens from the on-chain factory registry '
            f'(exhaustive) · <b>{p["pools_total"]:,}</b> stock-paired pools from a chain-wide '
            f'sweep{" (LOWER BOUND — " + str(p["pools_gaps"]) + " unscanned range(s))" if p["pools_gaps"] else " (exhaustive)"} · '
            f'<b>{p["pools_priced"]}</b> priced by GeckoTerminal, {p["pools_unindexed"]} not indexed by it · '
            f'<b>{p["impostors"]}</b> tokens impersonate a registry ticker. '
            f'Discovery is chain-derived; vendor pair listings are capped and miss live pools.')

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Robinhood Chain — Stock Tokens &amp; Stock-Paired Pools</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600;700&display=swap">
<style>{CSS}</style></head><body><div class="wrap">
<h1>Robinhood Chain — stock tokens &amp; stock-paired pools</h1>
<div class="sub">snapshot {snap['generated']}{' · liveness window ' + snap['window'] if snap.get('window') else ''}
 · data is inlined, this page makes no network calls</div>
<div class="prov">{prov}</div>
<div class="bar">
  <input type="search" id="q" placeholder="Search any ticker, name or address — e.g. GME, AAPL, 0x34c3…">
  <div class="tabs">
    <button data-tab="memes" class="on">Memes &times; Stocks</button>
    <button data-tab="stocks">Stocks</button>
    <button data-tab="pairs">All stock-paired pools</button>
  </div>
  <div class="tf">{''.join(f'<button data-tf="{k}">{l}</button>' for k, l in
                            (('m5','5m'),('m15','15m'),('h1','1h'),('h4','4h'),('h24','24h')))}</div>
  <span class="dim" id="count"></span>
</div>
<div class="bar" id="filters">
  <span class="dim">launched</span>
  <input type="date" id="from"><span class="dim">→</span><input type="date" id="to">
  <label class="chk"><input type="checkbox" id="nomkt"> only tokens with a pool</label>
</div>
<div class="meta" id="metastrip">{meta_html}</div>
<div class="bar" id="memefilters">
  <span class="dim">quote stock</span>
  <input type="search" id="mquote" placeholder="all — or type GLD, SPY, AAPL…" style="min-width:200px">
  <label class="chk"><input type="checkbox" id="mdead"> include pools with no activity</label>
</div>
<div class="bar hide" id="pairfilters">
  <label class="chk"><input type="checkbox" id="dead"> include pools with no activity</label>
</div>
<div class="scroll"><table><thead id="thead"></thead><tbody id="tbody"></tbody></table></div>
<div class="note">A <b>meme &times; stock</b> pool is one whose non-stock leg is a memecoin rather
than a quote asset — pools against USDG, WETH or native ETH are the stock's own market and live in
the "All stock-paired pools" tab instead. Ticker collisions are common on this chain — the REGISTRY badge, not age, is
the proof of which token is the real one. Search groups every token sharing a ticker, oldest first,
and flags deployers that minted more than one of them.</div>
<script type="application/json" id="data">{payload_json}</script>
<script>{JS}</script>
</div></body></html>"""

    out = os.path.join(os.path.dirname(C.DATA), "dashboard.html")
    with open(out, "w") as fh:
        fh.write(html)
    print(f"wrote {out}  ({len(html)/1e6:.2f} MB, {len(tlist):,} tokens in search index)")


if __name__ == "__main__":
    main()
