# PakVerify — Online Deployment Guide

| Part | Where it runs | Notes |
|---|---|---|
| Frontend (React/Vite) | Vercel project **pakverify** (root: `frontend/`) | SPA rewrites in `frontend/vercel.json` |
| Backend API (Django) | Vercel project **pakverify-api** (root: `backend/`) | Django preset, `backend/vercel.json` |
| Database | Neon PostgreSQL project **pakverify** (`pakverify_db`) | `DATABASE_URL` env var |
| Uploads / certificate PDFs | Vercel Blob store **pakverify-files** (private) | `BLOB_READ_WRITE_TOKEN` (auto-linked) |
| Ledger | Neon (`blockchain_blockchainblock` hash chain) | unchanged |
| Public anchor (optional) | Ethereum Sepolia — `contracts/PakVerifyRegistry.sol` | `ETH_*` env vars, MetaMask verify button |
| Face models (InsightFace/DeepFace) + `ai-service/` | **Not on Vercel** (too large) | Docker / Hugging Face Spaces / Render |

Every push to `main` on GitHub redeploys both Vercel projects automatically.

## 1. Environment variables

All keys are documented in `backend/.env.example` and `frontend/.env.example`.
Real values live only in **Vercel → Project → Settings → Environment Variables**
(and your local `.env`, which is git-ignored). Never commit secrets.

After changing a variable in Vercel, redeploy the project for it to take effect.

## 2. Database migrations (Neon)

Vercel does not run migrations. Run them from your machine whenever a new
migration is added:

```bash
cd backend
pip install -r requirements.txt
# use the DIRECT (non -pooler) Neon connection string for migrations
set DATABASE_URL=postgresql://USER:PASSWORD@ep-xxxx.REGION.aws.neon.tech/pakverify_db?sslmode=require   # Windows
export DATABASE_URL=...                                                                               # macOS/Linux
python manage.py migrate
python manage.py seed_data            # demo accounts (first time only)
python manage.py seed_nadra           # dummy NADRA records
python manage.py seed_criminal_records
python create_admins.py               # province admins/staff
```

## 3. Ethereum anchoring with MetaMask (optional)

The database ledger keeps working without this. To add a public on-chain proof:

1. Install **MetaMask**, create a **new wallet used only for PakVerify**, switch to
   the **Sepolia** test network and get free test ETH from a Sepolia faucet.
2. Open <https://remix.ethereum.org>, create `PakVerifyRegistry.sol` and paste
   `contracts/PakVerifyRegistry.sol`.
3. *Solidity Compiler* tab → compiler `0.8.20+` → **Compile**.
4. *Deploy & Run* tab → Environment **Injected Provider – MetaMask** → **Deploy**
   → confirm in MetaMask. Copy the deployed contract address.
5. In Vercel → **pakverify-api** → Environment Variables set:
   - `ETH_CONTRACT_ADDRESS` = the contract address
   - `ETH_PRIVATE_KEY` = private key of that same deployer wallet
     (MetaMask → Account details → Show private key). Mark it **Sensitive**.
   - `ETH_RPC_URL` (default `https://ethereum-sepolia-rpc.publicnode.com`, or an
     Alchemy/Infura Sepolia URL), `ETH_CHAIN_ID=11155111`
6. Redeploy **pakverify-api**.

From then on each issued certificate's SHA-256 hash and ledger block hash are
written to the contract (`EthereumAnchor` table keeps the tx hash). The public
verification page (`/verify/<certificate-number>`) shows the Etherscan link and a
**Verify with MetaMask** button that reads the contract directly from the
visitor's wallet. No names or CNIC numbers go on-chain.

Only use a testnet wallet with test funds for this key.

## 4. Face recognition / AI service

`requirements.txt` (installed by Vercel) contains only the web stack plus
OpenCV/NumPy. The heavy models are in `requirements-ai.txt`, which the backend
Dockerfile installs. On Vercel, endpoints that need InsightFace/DeepFace return
an error until they are hosted elsewhere:

- Run the full stack with Docker (`docker compose up`) for the live demo, or
- Host `ai-service/` (and optionally the backend with `requirements-ai.txt`) on a
  container host with ≥2 GB RAM (Hugging Face Spaces Docker, Render, Railway,
  a VPS), then set `AI_SERVICE_URL` (backend) and `VITE_AI_API_BASE_URL`
  (frontend) to its URL.

## 5. Local development

Nothing changes: without `DATABASE_URL`, `VERCEL`, `BLOB_READ_WRITE_TOKEN` and
`ETH_*`, the backend uses SQLite/Docker Postgres, local `media/` and the
database-only ledger exactly as before. Install `requirements-ai.txt` locally for
face recognition.
