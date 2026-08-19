#!/usr/bin/env bash
# Resolve the public URL of a running Fargate preview task.
# Usage: scripts/preview_task_url.sh <cluster> <service>
# Prints http://<public-ip>:8000
#
# Pins the service's *current* task definition. After a replace (new image or
# bot command), list-tasks can still return the draining previous revision;
# curling that ENI times out with 0 bytes once ECS has stopped it.
set -euo pipefail

if [[ "${#}" -ne 2 ]]; then
  echo "usage: $0 <cluster> <service>" >&2
  exit 1
fi

CLUSTER="${1}"
SERVICE="${2}"
REGION="${AWS_REGION:-us-east-1}"

TASK_DEF=""
for _ in $(seq 1 60); do
  TASK_DEF="$(aws ecs describe-services \
    --cluster "${CLUSTER}" \
    --services "${SERVICE}" \
    --region "${REGION}" \
    --query 'services[0].taskDefinition' \
    --output text)"
  if [[ -n "${TASK_DEF}" && "${TASK_DEF}" != "None" ]]; then
    break
  fi
  sleep 5
done

if [[ -z "${TASK_DEF}" || "${TASK_DEF}" == "None" ]]; then
  echo "no task definition on ${CLUSTER}/${SERVICE}" >&2
  exit 1
fi

TASK=""
for _ in $(seq 1 60); do
  ARNS="$(aws ecs list-tasks \
    --cluster "${CLUSTER}" \
    --service-name "${SERVICE}" \
    --desired-status RUNNING \
    --region "${REGION}" \
    --query 'taskArns[]' \
    --output text)"
  if [[ -n "${ARNS}" && "${ARNS}" != "None" ]]; then
    # shellcheck disable=SC2086
    TASK="$(aws ecs describe-tasks \
      --cluster "${CLUSTER}" \
      --tasks ${ARNS} \
      --region "${REGION}" \
      --query "tasks[?taskDefinitionArn=='${TASK_DEF}' && lastStatus=='RUNNING'].taskArn | [0]" \
      --output text)"
    if [[ -n "${TASK}" && "${TASK}" != "None" ]]; then
      break
    fi
  fi
  sleep 5
done

if [[ -z "${TASK}" || "${TASK}" == "None" ]]; then
  echo "no RUNNING task on ${CLUSTER}/${SERVICE} for ${TASK_DEF}" >&2
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
