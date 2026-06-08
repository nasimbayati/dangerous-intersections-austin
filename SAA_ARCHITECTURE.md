# AWS SAA Study Guide — Austin Dangerous Intersections (Cloud-Native Architecture)

> **Course**: Stephane Maarek — AWS Certified Solutions Architect Associate  
> **Project**: Austin Dangerous Intersections Predictor  
> **Goal**: Use this real project as a concrete study vehicle for each SAA domain

This document maps every component of the project to a specific AWS service, exam topic, and
Maarek course section. Run through each section as you study the corresponding lectures.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Data Ingestion & Storage                                        │
│  S3 (crash CSV) → Lambda ETL → S3 (cleaned + model artifacts)  │
└─────────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Training Pipeline (on demand or scheduled)                      │
│  S3 raw data → SageMaker Training Job → S3 model.pkl            │
│  OR: Step Functions → Glue/Lambda (InsightFlow) → SageMaker     │
└─────────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Serving Layer                                                   │
│  API Gateway → Lambda (prediction) → DynamoDB (lookup table)   │
│  OR: ALB → ECS Fargate (Flask app) → ElastiCache (grid cache)  │
└─────────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Frontend & CDN                                                  │
│  CloudFront → S3 (static HTML/JS) OR ECS Flask                  │
└─────────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Cross-Cutting Concerns                                          │
│  IAM · CloudWatch · VPC · KMS · CloudTrail                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Domain 1 — Storage (S3, EBS, EFS, Glacier)

### This project's storage needs

| What | Why | AWS Service | SAA Topic |
|------|-----|-------------|-----------|
| Raw crash CSV (229K rows, ~150MB) | Durable object storage, infrequent reads after initial load | **S3 Standard** | Storage classes |
| Cleaned `grid_summary_v2.csv` + `intersection_lookup_v2.csv` | Fast re-reads by Flask/Lambda | **S3 Standard** or **S3 Intelligent-Tiering** | Lifecycle policies |
| Trained model `.pkl` files | Versioned artifacts, immutable after training | **S3 with versioning enabled** | S3 versioning |
| Historical crash exports (older years) | Rarely accessed | **S3 Glacier Instant Retrieval** | Glacier tiers |
| Notebook EBS volume (during SageMaker training) | Low-latency block storage attached to instance | **EBS gp3** | EBS vs S3 trade-off |

### SAA exam concepts to study here
- S3 storage class decisions (Standard vs IA vs Glacier) — the crash CSV access pattern is "write once, read often initially, then archive"
- S3 Lifecycle rules — auto-transition raw CSVs to S3-IA after 90 days
- S3 versioning — protects model artifacts from accidental overwrites
- S3 Transfer Acceleration — useful if uploading new crash dumps from City of Austin
- Pre-signed URLs — give Flask app temporary access to download model from S3 on startup
- **Maarek sections**: S3 deep dive, S3 advanced features

---

## Domain 2 — Compute (EC2, Lambda, ECS, Beanstalk)

### Decision: where does the Flask app run?

```
Flask Prediction App
    │
    ├── Option A: AWS Elastic Beanstalk
    │   ✅ Easiest to deploy (just zip and upload)
    │   ✅ Auto-manages EC2, ALB, Auto Scaling
    │   ❌ Less control over networking
    │   → Good study angle: Beanstalk environments, health checks, rolling deploys
    │
    ├── Option B: ECS Fargate (Recommended for SAA study)
    │   ✅ Serverless containers — no EC2 management
    │   ✅ Deep VPC/ALB integration
    │   ✅ Task definitions map cleanly to "Flask container + model volume"
    │   → Good study angle: ECS vs EKS, task roles vs instance roles, Fargate pricing
    │
    └── Option C: EC2 + Auto Scaling Group
        ✅ Full control, teaches most SAA topics (AMIs, Launch Templates, ASG policies)
        ❌ Operational overhead
        → Good study angle: ASG lifecycle hooks, mixed instance policies
```

### This project's Lambda opportunities

| Lambda function | Trigger | Purpose | SAA topic |
|-----------------|---------|---------|-----------|
| `clean-and-process` | S3 PutObject (new CSV) | Run InsightFlow cleaning pipeline | Event-driven architecture |
| `predict-risk` | API Gateway POST /predict | Stateless prediction for single location | Lambda + API Gateway |
| `batch-predict` | S3 PutObject (.csv upload) | Process batch prediction files | Async Lambda with S3 |
| `retrain-trigger` | EventBridge (monthly cron) | Kick off SageMaker training job | EventBridge + Lambda |

### SAA exam concepts to study here
- Lambda concurrency limits, cold starts, memory/timeout configuration
- Lambda layers — package `scikit-learn`, `xgboost`, `pandas` as a reusable layer
- ECS task definition: CPU/memory, IAM task role, logging to CloudWatch
- ALB target groups: path-based routing (`/predict` → Lambda, `/upload` → ECS)
- Auto Scaling: target tracking vs step scaling for the ECS service
- **Maarek sections**: EC2, Lambda, ECS/Fargate, Beanstalk

---

## Domain 3 — Databases & Caching

### This project's data access patterns

| Data | Access Pattern | Service | Why not another option |
|------|---------------|---------|------------------------|
| `intersection_lookup_v2.csv` (300K+ entries) | Key lookup by (primary_street, secondary_street) | **DynamoDB** | Perfect partition key use case; sub-ms latency; no joins needed |
| `grid_summary_v2.csv` (18K grid cells) | Lookup by (lat_grid, lon_grid) | **DynamoDB** or **ElastiCache Redis** | Fit entirely in cache; prediction hot path needs < 5ms |
| Prediction audit log | Write-heavy, time-series, append-only | **DynamoDB** with TTL | Cheaper than RDS for this schema |
| If crash data needed relationally | Complex SQL queries across many columns | **RDS Aurora PostgreSQL** | Only if you need joins + analytics |

### DynamoDB design for intersection lookup

```
Table: intersection-lookup
  Partition key: primary_street (String)    # e.g. "n lamar blvd"
  Sort key:      secondary_street (String)  # e.g. "w 6th st"
  Attributes:    lat_grid, lon_grid

Table: grid-summary
  Partition key: lat_grid_lon_grid (String) # e.g. "30.289_-97.723"
  Attributes:    total_crashes, death_rate, injury_rate, avg_speed_limit, ...

Table: prediction-audit
  Partition key: request_id (String)        # UUID
  Sort key:      timestamp (Number)         # Unix epoch
  TTL:           expires_at (Number)        # auto-delete after 90 days
```

### ElastiCache Redis (prediction hot path)

```
Cache key:   "grid:{lat_grid}:{lon_grid}"
Cache value: JSON of grid row features (total_crashes, death_rate, ...)
TTL:         3600 seconds (refreshed on retrain)

Why: Lambda prediction function loads grid features on every call.
     Caching removes the DynamoDB read on the hot path.
```

### SAA exam concepts to study here
- DynamoDB: partition key design, on-demand vs provisioned capacity, GSIs
- DynamoDB Streams + Lambda: trigger downstream processing when a new crash record lands
- DynamoDB TTL: auto-expire old prediction logs without manual cleanup
- ElastiCache: Redis vs Memcached decision, cluster mode, eviction policies
- RDS Multi-AZ vs Read Replicas: when to use each (Multi-AZ = HA, Read Replicas = scale reads)
- **Maarek sections**: DynamoDB, ElastiCache, RDS & Aurora

---

## Domain 4 — Networking (VPC, Route 53, CloudFront, API Gateway)

### VPC architecture for this project

```
VPC: 10.0.0.0/16
│
├── Public Subnets (10.0.1.0/24, 10.0.2.0/24) — 2 AZs
│   ├── ALB (internet-facing)
│   └── NAT Gateway (for private subnet outbound)
│
├── Private Subnets (10.0.3.0/24, 10.0.4.0/24) — 2 AZs
│   ├── ECS Fargate tasks (Flask app)
│   ├── Lambda (via VPC config — needed to reach ElastiCache)
│   └── ElastiCache cluster
│
└── Database Subnets (10.0.5.0/24, 10.0.6.0/24) — 2 AZs
    └── DynamoDB VPC Endpoint (no internet traffic for DB reads)

Security Groups:
  - ALB SG:      inbound 443 from 0.0.0.0/0
  - ECS task SG: inbound 5000 from ALB SG only
  - Cache SG:    inbound 6379 from ECS task SG only
```

### CloudFront distribution

```
Origins:
  /api/*      → ALB (dynamic — Flask predictions)
  /*          → S3 (static assets: HTML, CSS, JS, Folium maps)

Cache behaviors:
  /api/map-data  TTL=300s (map data changes infrequently)
  /api/predict   TTL=0    (always fresh, user-specific)
  /*.html        TTL=86400s

Why: Serves Folium map HTML files (heatmap_all_crashes.html, etc.)
     from S3 at edge. Reduces Flask/ECS load for static content.
```

### SAA exam concepts to study here
- VPC CIDR planning, subnet sizing (public vs private vs DB)
- Internet Gateway vs NAT Gateway (know the cost difference)
- Security Groups vs NACLs (stateful vs stateless)
- VPC Endpoints: Gateway (S3/DynamoDB) vs Interface (other services) — saves NAT Gateway cost
- Route 53: weighted routing (canary deploys), latency-based, failover
- CloudFront: origins, cache behaviors, signed URLs, OAC for S3
- API Gateway: REST vs HTTP API, throttling, usage plans, caching
- **Maarek sections**: VPC, Route 53, CloudFront, API Gateway

---

## Domain 5 — Integration & Messaging (SQS, SNS, EventBridge, Step Functions)

### Event-driven pipeline for new crash data

```
City of Austin uploads new crash CSV
    ↓
S3 PutObject event
    ↓
EventBridge rule matches s3://bucket/raw/*.csv
    ↓
Step Functions state machine:
  1. Lambda: InsightFlow cleaning (normalize_missing_values, check_duplicates)
  2. Lambda: Feature engineering + grid aggregation
  3. SageMaker Training Job: retrain XGBoost model
  4. Lambda: Copy new model.pkl to S3 models/ prefix
  5. Lambda: Invalidate ElastiCache (flush stale grid features)
  6. SNS: Notify admin email "Model retrained successfully"
    ↓
ECS task picks up new model on next restart (or via S3 watch)
```

### Batch prediction with SQS

```
User uploads batch CSV
    ↓
Flask (ECS) validates and pushes each row as SQS message
    ↓
Lambda consumer (max_concurrency=10) processes each message:
  - Resolves location → grid lookup (DynamoDB/Cache)
  - Runs XGBoost prediction
  - Writes result to DynamoDB
    ↓
Flask polls DynamoDB for results → returns completed table to user
```

### SAA exam concepts to study here
- SQS: Standard vs FIFO, visibility timeout, dead-letter queues (DLQ), long polling
- SNS: fan-out pattern (one publish → multiple subscribers), SNS + SQS decoupling
- EventBridge: event buses, rules, scheduled events (cron syntax for monthly retraining)
- Step Functions: Standard vs Express workflows, error handling, parallel states
- Kinesis: if crash data arrived as a real-time stream (vs batch CSV)
- **Maarek sections**: SQS, SNS, EventBridge, Step Functions

---

## Domain 6 — IAM & Security (Least Privilege, Encryption, Cognito)

### IAM roles for this project

```
Role: ecs-flask-task-role
  Policies:
    - s3:GetObject on models/* (download model.pkl at startup)
    - dynamodb:GetItem, Query on intersection-lookup and grid-summary
    - elasticache:Connect (via VPC, no IAM policy needed — network controls it)
    - logs:PutLogEvents (CloudWatch logging)

Role: lambda-clean-and-process-role
  Policies:
    - s3:GetObject on raw/*
    - s3:PutObject on clean/* and models/*
    - dynamodb:BatchWriteItem on grid-summary
    - sagemaker:CreateTrainingJob

Role: sagemaker-training-role
  Policies:
    - s3:GetObject on clean/* (training data)
    - s3:PutObject on models/* (save trained model)
    - logs:CreateLogGroup, PutLogEvents
```

### Encryption at rest and in transit

| Data | Encryption | Key management |
|------|-----------|----------------|
| S3 crash CSV | SSE-S3 (default) or SSE-KMS | AWS managed key or CMK |
| S3 model artifacts | SSE-KMS with CMK | Rotate annually |
| DynamoDB tables | AES-256 (always on) | AWS owned or CMK |
| ElastiCache | In-transit TLS + at-rest encryption | CMK |
| ECS → ALB | HTTPS with ACM certificate | ACM auto-renews |

### SAA exam concepts to study here
- IAM roles vs users (EC2/ECS should never use access keys — use instance/task roles)
- Resource-based policies vs identity-based policies (S3 bucket policy vs IAM policy)
- KMS: CMK vs AWS managed key, key policies, envelope encryption
- Secrets Manager vs SSM Parameter Store (API keys, DB passwords)
- ACM: certificate provisioning for HTTPS, CloudFront, ALB
- **Maarek sections**: IAM, KMS, SSM, Secrets Manager

---

## Domain 7 — Monitoring & Operations (CloudWatch, X-Ray, CloudTrail)

### CloudWatch setup for this project

```
Metrics to track:
  - ECS CPU/Memory utilization (alarm if > 80% for 5 min → scale out)
  - Lambda duration (p95 > 3000ms → investigate cold start or memory)
  - Lambda error rate (> 1% → page on-call)
  - DynamoDB SuccessfulRequestLatency (> 10ms → check provisioned capacity)
  - SQS ApproximateNumberOfMessagesVisible (> 1000 → scale Lambda consumer)

CloudWatch Logs:
  - /ecs/flask-app → Flask request logs, prediction outputs
  - /aws/lambda/predict-risk → per-request timing
  - /aws/sagemaker/TrainingJobs → retraining run details

Dashboard: "Dangerous Intersections - Production"
  - Requests/minute (from API Gateway)
  - Prediction latency p50/p95/p99
  - Cache hit rate (ElastiCache)
  - Model version in use (custom metric from Lambda)
```

### SAA exam concepts to study here
- CloudWatch metrics vs logs vs alarms vs dashboards
- CloudWatch Alarms: threshold vs anomaly detection, composite alarms
- CloudTrail: API call audit, S3 data events (who downloaded the model?)
- X-Ray: distributed tracing across API Gateway → Lambda → DynamoDB
- AWS Config: drift detection (did someone manually change a security group?)
- **Maarek sections**: CloudWatch, CloudTrail, X-Ray

---

## Domain 8 — High Availability & Disaster Recovery

### HA design for this project

| Component | HA strategy | RPO/RTO |
|-----------|------------|---------|
| ECS Fargate | 2+ tasks across 2 AZs, ALB health checks | < 30s (automatic replacement) |
| DynamoDB | Multi-region Global Tables (if needed) | Near-zero RPO with Global Tables |
| ElastiCache Redis | Multi-AZ with automatic failover | < 1 min (promotion of replica) |
| S3 (model artifacts) | Cross-Region Replication to us-west-2 | RPO = 15 min (async replication) |
| Lambda | Inherently multi-AZ | Near-zero |

### SAA exam concepts to study here
- RDS Multi-AZ vs Read Replicas (HA vs scalability — common exam trap)
- S3 CRR (Cross-Region Replication) — requirements: versioning must be on both buckets
- ElastiCache: primary + replica, automatic failover, cluster mode enabled
- Route 53 failover routing with health checks
- Pilot Light vs Warm Standby vs Multi-Site Active-Active
- **Maarek sections**: Disaster Recovery, HA patterns

---

## Domain 9 — Cost Optimization

### Right-sizing decisions

| Component | Cost consideration | Decision |
|-----------|-------------------|---------|
| ECS Fargate | Pay per vCPU/memory-second | 0.5 vCPU / 1GB = ~$18/month (light load) |
| Lambda prediction | Pay per invocation + duration | XGBoost inference < 200ms → very cheap |
| DynamoDB | On-demand (no traffic prediction needed) vs provisioned | On-demand for dev; provisioned for prod if traffic is predictable |
| S3 crash CSV archive | Glacier vs IA | Glacier Instant Retrieval if accessed < monthly |
| NAT Gateway | $0.045/GB — expensive for high volume | Use VPC Gateway Endpoint for S3/DynamoDB instead |

### SAA exam concepts to study here
- Reserved Instances vs Savings Plans vs On-Demand vs Spot
- Lambda pricing model (free tier: 1M requests/month — this project stays free under light usage)
- S3 requester-pays (if sharing crash data with other teams)
- Compute Optimizer recommendations
- **Maarek sections**: Cost optimization, Trusted Advisor

---

## Quick Reference: Maarek Course → This Project

| Maarek Section | This Project Touchpoint |
|---------------|------------------------|
| S3 storage classes | Crash CSV lifecycle (Standard → IA → Glacier) |
| EC2 & AMIs | ECS launch type comparison; SageMaker training instance |
| Lambda | Prediction endpoint, ETL trigger, batch processor |
| ECS & Fargate | Flask app deployment, container-native serving |
| API Gateway | REST API for `/predict` and `/upload` endpoints |
| DynamoDB | Intersection lookup table, grid summary, audit log |
| ElastiCache | Grid feature cache for prediction hot path |
| VPC & Subnets | Isolate Flask, Lambda, cache in private subnets |
| CloudFront | Serve Folium map HTML files from S3 edge |
| IAM roles | Task role (ECS), execution role (Lambda), training role (SM) |
| SQS & SNS | Batch prediction queue, retrain completion notification |
| Step Functions | Orchestrate clean → train → deploy pipeline |
| EventBridge | Trigger retraining when new CSV lands in S3 |
| CloudWatch | Alarms on prediction latency, Lambda errors, ECS CPU |
| Route 53 | Domain + health-check failover for Flask endpoint |
| KMS | Encrypt S3 model artifacts and DynamoDB tables |

---

## Study Tips

1. **Sketch the architecture** from memory for each domain before reading this doc.
2. **Cost estimation exercise**: Use the AWS Pricing Calculator to price this setup for 1,000 predictions/day.
3. **Security review**: For each service, ask "what IAM permissions does it need?" and "is data encrypted at rest and in transit?"
4. **Trade-off practice**: For each component, write down 2 alternative services and why you chose the one you did (e.g. DynamoDB vs RDS vs ElastiCache for the lookup table).
5. **Failure mode exercise**: For each component, ask "what happens if this fails?" and verify your HA design handles it.
