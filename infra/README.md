# Toke EC2 Infrastructure Scripts

Deployment and execution scripts for the toke corpus generation and training pipelines on EC2.

## Prerequisites

- **AWS CLI** configured with appropriate credentials
- **SSH key** for the EC2 instances (set via `$SSH_KEY`)
- **rsync** installed locally
- **tmux** installed on the EC2 instances (installed by `setup.sh`)
- EC2 instances running Ubuntu 24.04

### EC2 Instances

| Role | Purpose | Env Variable |
|------|---------|-------------|
| Corpus generation | Multi-provider LLM API calls, transpilation, validation | `CORPUS_EC2_HOST` |
| Training | Model fine-tuning (QLoRA/LoRA) | `TRAINING_EC2_HOST` |

## Environment Variables

Export these before running any script:

```bash
export SSH_KEY="~/.ssh/your-key.pem"
export SSH_USER="ubuntu"                    # default
export CORPUS_EC2_HOST="<corpus-instance>"  # hostname or IP
export TRAINING_EC2_HOST="<train-instance>" # hostname or IP
```

You can put these in a local shell rc file or a `.envrc` (not committed).

## Scripts

### 1. Initial Instance Setup

Run `setup.sh` on the instance (via SSH) to install system packages, create the Python venv, and set up directories:

```bash
ssh -i $SSH_KEY $SSH_USER@$CORPUS_EC2_HOST 'sudo bash -s' < infra/setup.sh
```

### 2. Deploy Corpus Generation Pipeline

```bash
./infra/deploy_corpus_gen.sh
```

What it does:
- Rsyncs the toke-corpus pipeline code to `/opt/toke-corpus/`
- Creates a Python venv and installs dependencies
- Copies the tkc compiler binary (set `TKC_LOCAL` for a custom path)
- Copies the `.env` file with API keys (set `ENV_FILE` for a custom path)

### 3. Deploy Training Pipeline

```bash
./infra/deploy_training.sh
```

What it does:
- Rsyncs the toke-model repo to `/opt/toke-model/`
- Rsyncs corpus data files (`.jsonl`) to `/opt/toke-corpus-data/`
- Installs training dependencies (CUDA or MLX)
- Runs a pre-flight checklist (GPU, CUDA, disk space, memory)

### 4. Run API-Based Corpus Generation

```bash
./infra/run_api_generation.sh
./infra/run_api_generation.sh --tasks 10000 --cost-limit 200
./infra/run_api_generation.sh --resume
./infra/run_api_generation.sh --dry-run
```

Options:
- `--tasks N` — number of tasks to generate (default: 50000)
- `--cost-limit N` — maximum API spend in USD (default: 500.00)
- `--config FILE` — config file name (default: config.yaml)
- `--resume` — resume from last checkpoint
- `--dry-run` — print config and exit

The pipeline runs in a tmux session (`corpusgen`) that persists across SSH disconnects.

### 5. Run Model Training

```bash
./infra/run_training.sh
./infra/run_training.sh --config 7b_mlx.yaml
./infra/run_training.sh --epochs 5 --lr 2e-5
./infra/run_training.sh --skip-merge --dry-run
```

Options:
- `--config FILE` — training config in `finetune/configs/` (default: 7b.yaml)
- `--epochs N` — override epoch count
- `--lr RATE` — override learning rate
- `--batch-size N` — override batch size
- `--skip-merge` — skip corpus merge step
- `--dry-run` — print config and exit

Training runs in a tmux session (`training`) that persists across SSH disconnects.

## API Key Configuration

Create a `.env` file at the toke-corpus repo root (never committed):

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
DEEPSEEK_API_KEY=sk-...
XAI_API_KEY=xai-...
GOOGLE_API_KEY=AIza...
```

The deploy script copies this to the instance with 600 permissions.

### Required Keys by Provider

| Provider | Key Variable | Models | Tier |
|----------|-------------|--------|------|
| Anthropic | `ANTHROPIC_API_KEY` | claude-haiku-4-5 | 2 (quality) |
| OpenAI | `OPENAI_API_KEY` | gpt-4.1-mini | 1 (volume) |
| DeepSeek | `DEEPSEEK_API_KEY` | deepseek-chat | 1 (volume) |
| xAI | `XAI_API_KEY` | grok | 1 (volume) |
| Google | `GOOGLE_API_KEY` | gemini-2.5-flash | 1 (volume) |

You do not need all keys. The pool manager allocates work to available providers.

## Cost Estimates

Based on `config.example.yaml` pricing and a 50k task target:

| Provider | Input $/1M | Output $/1M | Est. Cost (50k tasks) |
|----------|-----------|------------|----------------------|
| Anthropic (Haiku) | $0.80 | $4.00 | ~$80-120 |
| OpenAI (4.1-mini) | $0.40 | $1.60 | ~$30-50 |
| Gemini (Flash) | $0.15 | $0.60 | ~$10-20 |
| DeepSeek | $0.27 | $1.10 | ~$15-25 |

Total estimated cost for a full 50k-task run with the default tier mix: **$150-250 USD**.

The pipeline has a hard `cost_limit` (default $500) that halts generation if exceeded.

## Monitoring Progress

### Attach to tmux session

```bash
ssh -i $SSH_KEY $SSH_USER@$CORPUS_EC2_HOST 'tmux attach -t corpusgen'
ssh -i $SSH_KEY $SSH_USER@$TRAINING_EC2_HOST 'tmux attach -t training'
```

### Tail logs

```bash
ssh -i $SSH_KEY $SSH_USER@$CORPUS_EC2_HOST 'tail -f /opt/toke-corpus/logs/generation_*.log'
ssh -i $SSH_KEY $SSH_USER@$TRAINING_EC2_HOST 'tail -f /opt/toke-model/checkpoints/run_*/training_*.log'
```

### Check corpus size

```bash
ssh -i $SSH_KEY $SSH_USER@$CORPUS_EC2_HOST 'wc -l /opt/toke-corpus/corpus/*.jsonl'
```

### Check GPU usage (training)

```bash
ssh -i $SSH_KEY $SSH_USER@$TRAINING_EC2_HOST 'nvidia-smi'
```

## Downloading Results

```bash
# Corpus data
rsync -avz -e "ssh -i $SSH_KEY" $SSH_USER@$CORPUS_EC2_HOST:/opt/toke-corpus/corpus/ ./corpus/

# Metrics
rsync -avz -e "ssh -i $SSH_KEY" $SSH_USER@$CORPUS_EC2_HOST:/opt/toke-corpus/metrics/ ./metrics/

# Training checkpoint
rsync -avz -e "ssh -i $SSH_KEY" $SSH_USER@$TRAINING_EC2_HOST:/opt/toke-model/checkpoints/ ./checkpoints/
```

## Troubleshooting

**SSH connection refused** — Ensure the EC2 instance is running and the security group allows SSH (port 22) from your IP.

**tmux session not found** — The pipeline has finished (or crashed). Check the log file for details.

**API rate limits** — The dispatch pool handles retries with exponential backoff. If persistent, reduce concurrency in the config or switch to batch mode.

**Out of disk space** — Corpus JSONL files grow over time. Monitor with `df -h`. For training, ensure at least 50 GB free for model weights and checkpoints.

**CUDA out of memory** — Reduce batch size (`--batch-size 1`) or use a smaller model config. Check GPU memory with `nvidia-smi`.
