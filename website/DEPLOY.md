# Ocean Agent — website

Static one-page site. Single self-contained file: `index.html` (fonts load from
Google Fonts CDN; everything else is inline). No build step.

## Preview locally
Just open `index.html` in a browser, or serve the folder:
```bash
python -m http.server 8000 --directory website
# then open http://localhost:8000
```

## Deploy on Vercel (chosen) — free, no domain needed yet

`vercel.json` in this folder is already set up (clean URLs, no trailing slash).

**Option A — CLI (fastest)**
```bash
npm i -g vercel        # once
cd website
vercel                 # first run: log in + link project → preview URL
vercel --prod          # promote to production → *.vercel.app URL
```

**Option B — Dashboard (from GitHub)**
1. Push this repo to GitHub.
2. vercel.com → Add New → Project → import the repo.
3. **Root Directory: `website`** (important — the site lives in this subfolder).
4. Framework preset: **Other** (it's static; no build command needed).
5. Deploy → live at `https://<project>.vercel.app`.

## Other free hosts (if ever needed)
- **GitHub Pages**: Settings → Pages → source `main` + `/website` (or move
  `index.html` to `/docs`). URL `https://<user>.github.io/<repo>/`.
- **Netlify / Cloudflare Pages**: drag the `website` folder onto their dashboard.

## Add a custom domain later
Register a domain (e.g. oceanagent.xyz / .fi, ~$10–15/yr), then in Vercel:
Project → Settings → Domains → add it, and set the DNS records Vercel shows
(or buy the domain through Vercel to skip DNS setup). No code change needed;
the site stays the same.

## Content accuracy notes (kept honest)
Adjusted from the original draft to match the real system:
- Final stop shown as **−20%** (matches `policy.yaml` `fatal_loss_pct: 0.20`),
  not −50%.
- "126k **measurements**" (backtest matrix samples), not "126k trades".
- Print feature reworded to "a verdict on whether an offer is worth it — and when
  it isn't" (the tool exists to warn you off bad offers).
- Live track record honestly marked **testnet**; no win-rate / PnL numbers claimed.

If the competitor comparison table should be toned down (it names real
companies), edit the `#compare` section — everything else can stay.
