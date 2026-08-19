#!/usr/bin/env bash
# Resolve the public URL of a running Fargate preview task.
# Usage: scripts/preview_task_url.sh <cluster> <service>
# Prints http://<public-ip>:8000
set -euo pipefail

if [[ "${#}" -ne 2 ]]; then
  echo "usage: $0 <cluster> <service>" >&2
  exit 1
fi

CLUSTER="${1}"
SERVICE="${2}"
REGION="${AWS_REGION:-us-east-1}"
TASK=""

for _ in $(seq 1 60); do
  TASK="$(aws ecs list-tasks \
    --cluster "${CLUSTER}" \
    --service-name "${SERVICE}" \
    --desired-status RUNNING \
    --region "${REGION}" \
    --query 'taskArns[0]' \
    --output text)"
  if [[ -n "${TASK}" && "${TASK}" != "None" ]]; then
    break
  fi
  sleep 5
done

if [[ -z "${TASK}" || "${TASK}" == "None" ]]; then
  echo "no RUNNING task on ${CLUSTER}/${SERVICE}" >&2
  exit 1
fi

aws ecs wait tasks-running \
  --cluster "${CLUSTER}" \
  --tasks "${TASK}" \
  --region "${REGION}"

ENI="$(aws ecs describe-tasks \
  --cluster "${CLUSTER}" \
  --tasks "${TASK}" \
  --region "${REGION}" \
  --query "tasks[0].attachments[0].details[?name=='networkInterfaceId'].value | [0]" \
  --output text)"

if [[ -z "${ENI}" || "${ENI}" == "None" ]]; then
  echo "task ${TASK} has no networkInterfaceId" >&2
  exit 1
fi

IP="$(aws ec2 describe-network-interfaces \
  --network-interface-ids "${ENI}" \
  --region "${REGION}" \
  --query 'NetworkInterfaces[0].Association.PublicIp' \
  --output text)"

if [[ -z "${IP}" || "${IP}" == "None" ]]; then
  echo "eni ${ENI} has no public IP" >&2
  exit 1
fi

echo "http://${IP}:8000"
