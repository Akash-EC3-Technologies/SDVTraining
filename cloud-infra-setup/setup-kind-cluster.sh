#!/bin/env bash

kind delete cluster --name ota-cluster

kind create cluster \
    --name ota-cluster \
    --config ./kind-cluster-config.yaml

kubectl create namespace ingress-nginx
kubectl create namespace mqtt
kubectl create namespace registry

kubectl create secret tls ota-lab-wildcard \
    --namespace ingress-nginx \
    --cert ~/certs/tls.crt \
    --key ~/certs/tls.key

kubectl create secret generic -n mqtt mqtt-broker-certs \
    --from-file=root-ca.crt=/home/akash/.step/certs/root_ca.crt \
    --from-file=tls.crt=/home/akash/certs/tls.crt \
    --from-file=tls.key=/home/akash/certs/tls.key

helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
    --namespace ingress-nginx \
    --create-namespace \
    -f nginx-ingress/helm-values/values.yaml

while ! kubectl rollout status  -n ingress-nginx  deployment/ingress-nginx-controller --timeout=1s >/dev/null 2>&1; do
  sleep 2
done

kubectl apply -f registry
kubectl apply -f mqtt-broker
