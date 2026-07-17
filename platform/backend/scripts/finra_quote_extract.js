// FINRA bond-quote extractor — feeds hazard/data/bond_quotes.json (manual drop-file).
//
// Usage (browser console or claude-in-chrome javascript_tool):
//   1. Open https://www.finra.org/finra-data/fixed-income/corp-and-agency
//      (accept the Fixed Income Data User Agreement checkbox / User Notice pop-up).
//   2. Paste this whole file, then call:
//        await finraQuotes({issuer: 'LUCID'})            // by issuer-name contains
//        await finraQuotes({symbols: ['AAL5147600']})    // by TRACE symbol(s)
//   3. Paste the returned JSON into bond_quotes.json under the ticker key.
//
// How it works: the page's FINRA-DATA-GRID Angular element owns an authenticated
// datasource (services-dynarep.ddwa.finra.org needs a per-request token, so raw
// fetch/XHR 401s). We call the grid's own getRows() with a crafted request.
// ponytail: DOM-coupled to the DDWA grid internals; breaks if FINRA reshapes the
// widget — re-derive via the deep-shadow scan below if so.

async function finraQuotes({issuer, symbols} = {}) {
  const deep = (root, pred, acc = [], d = 0) => {
    if (!root || d > 9) return acc;
    for (const el of root.querySelectorAll('*')) {
      if (pred(el)) acc.push(el);
      if (el.shadowRoot) deep(el.shadowRoot, pred, acc, d + 1);
    }
    return acc;
  };
  const host = deep(document.querySelector('finra-dynamic-reporting-explorer').shadowRoot,
                    el => el.tagName === 'FINRA-DATA-GRID')[0];
  const grid = host._ngElementStrategy.componentRef.instance;
  const getRows = grid.activeDatasource.datasource.getRows.bind(grid.activeDatasource.datasource);

  const cols = ['issueSymbolIdentifier', 'cusip', 'issuerName', 'couponRate', 'maturityDate',
                'isCallable', 'isConvertible', 'is144A', 'lastSalePrice', 'lastSaleYield',
                'lastTradeDate', 'moodysRating', 'moodyRatingDate', 'standardAndPoorsRating']
    .map(id => ({id, displayName: id}));

  const q = filter => new Promise((resolve, reject) => {
    getRows({columns: cols, filter, sort: [{colId: 'issuerName', sort: '+'}],
             group: {rowGroupCols: [], groupKeys: [], otherGroup: []},
             page: {startRow: 0, endRow: 50}})
      .subscribe({next: d => resolve(d.data || []), error: reject});
    setTimeout(() => reject(new Error('timeout')), 15000);
  });

  let rows = [];
  if (issuer) rows = await q({issuerName: {filterType: 'text', type: 'contains', filter: issuer}});
  for (const s of symbols || [])
    rows.push(...await q({issueSymbolIdentifier: {filterType: 'text', type: 'contains', filter: s}}));

  const asOf = new Date().toISOString().slice(0, 10);
  return rows.map(r => ({
    symbol: r.issueSymbolIdentifier,
    cusip: r.cusip,
    issuer: r.issuerName,
    coupon: r.couponRate != null ? +(+r.couponRate).toFixed(3) : null,
    maturity: r.maturityDate,
    callable: r.isCallable === 'Y',
    convertible: r.isConvertible === 'Y',
    last_price: r.lastSalePrice != null ? +(+r.lastSalePrice).toFixed(3) : null,
    last_yield: r.lastSaleYield != null ? +(+r.lastSaleYield).toFixed(3) : null,
    last_trade: r.lastTradeDate,
    rating: r.moodysRating ? `${r.moodysRating} (Moody's ${r.moodyRatingDate || 'n.d.'})`
           : r.standardAndPoorsRating ? `${r.standardAndPoorsRating} (S&P)` : 'NR',
    source: `https://www.finra.org/finra-data/fixed-income/bond?symbol=${r.issueSymbolIdentifier}&bondType=CA`,
    as_of: asOf,
  }));
}
