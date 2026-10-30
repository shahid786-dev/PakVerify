# AI-Based Police and Citizen Interaction

University FYP project for police verification workflows, AI-assisted face verification, and citizen services.

## Project Structure

```text
frontend/       React frontend
backend/        Django APIs and application logic
ai-service/     AI-related processing
data/           Dummy identity dataset
certificates/   Certificate templates and generated certificates
docs/           Project documentation when added
```

## Running the Project

- Use `START_ALL.bat` for the local frontend, backend, and AI service launcher.
- Use `docker-compose.yml` to run the containerized services.
## Live Deployment

- Frontend: https://pakverify-ten.vercel.app
- Backend API (Django, Swagger at `/api/docs/`): Vercel project `pakverify-api`
- Database: Neon PostgreSQL

See [DEPLOYMENT.md](DEPLOYMENT.md) for environment variables, migrations,
the optional Ethereum/MetaMask anchoring and hosting the face-recognition service.
