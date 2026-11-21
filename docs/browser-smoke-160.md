# Browser smoke for #160

1. Open `/catalog?q=foo&page=2`, wait for cards, note scroll position.
2. Open a movie/series detail page, then press browser Back: cards render immediately, filters/page/scroll restored, no full reload spinner.
3. Press Forward then Back: same restored state.
4. On detail page, add/remove watchlist or mark/unmark watched, then Back: catalogue/watchlist does not show pre-mutation rows.
5. Change country/language or filters, then Back: no stale cross-contaminated results.
6. Wait beyond cache TTL or flood navigation to trigger LRU eviction, then Back: fresh fetch occurs.
