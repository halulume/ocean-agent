# Disclaimer

Read this before connecting the software to an account holding real funds.

## This is not financial advice

Nothing this software outputs is investment advice, a recommendation, or a
solicitation to trade. Win rates, expected values, and "recommended settings"
are **measurements of past price data**, not predictions. The authors are not
licensed financial advisors and do not know your circumstances.

## It places real orders with real money

Given an API key with trading permission, this software submits live orders and
can open leveraged positions without asking first, that is what the autonomous
mode is for. Losses are real and can exceed what you expected. You are
responsible for every order placed under your key, including orders you did not
personally review.

Start with `--dry`, which decides but places no orders, and then with size you
are willing to lose. Watch it run and understand what it does before you let it
trade unattended. A testnet endpoint
(`PACIFICA_BASE_URL=https://test-api.pacifica.fi`) is available if you prefer to
rehearse there, using its own separate keys.

## Leveraged perpetual futures can lose more than the margin posted

Positions can be liquidated. Stop-loss orders are not guarantees: in a fast
market or a price gap the fill can be far worse than the stop price, or the
stop may not fill at all. Isolated margin limits the damage to one position's
margin; it does not prevent that position from being lost entirely.

## Measured edges are small and may not persist

The strategy logic in this repository is built on statistically measured
signals. Measurement showed the realistic ceiling in this market is around a
**58% win rate**, and that backtested figures above ~55% did not survive
out-of-sample testing. Edges that small are fragile: they can disappear when the
market regime changes, and they are easily erased by fees, slippage, and thin
order books. Past measurement does not guarantee future results.

## Undocumented endpoints

Some features (notably Print) call web APIs that the exchange does not
document. They can change or break without notice. Treat anything depending on
them as experimental.

## Your keys are your responsibility

The software never transmits your keys anywhere except to the exchange. Use an
**API key** (revocable at
[app.pacifica.fi/apikey](https://app.pacifica.fi/apikey)) rather than a wallet
key. Keep it in a `.env` file outside version control. Ocean Agent never
withdraws, but the permissions the key itself carries are set by the Exchange,
not by us. Anyone who obtains it can trade your account.

## No warranty

This software is provided "as is" under the MIT License, without warranty of any
kind. The authors are not liable for any loss arising from its use, including
losses caused by bugs, incorrect calculations, exchange outages, network
failures, or misconfiguration.

## Regulatory

Derivatives trading is restricted or prohibited in some jurisdictions. It is
your responsibility to determine whether your use of this software and of
Pacifica is lawful where you are.

---

**If you are not prepared to lose the funds in the connected account, do not
connect it.**
