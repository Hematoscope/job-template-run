# Makefile for local testing with kind, Helm, and Chainsaw

KIND_CLUSTER_NAME=job-template-run-test
NAMESPACE=job-template-run
HELM_CHART=charts/job-template-run
HELM_RELEASE=job-template-run
DOCKER_IMAGE=ghcr.io/hematoscope/job-template-run


.PHONY: kind kind-down helm-install helm-uninstall build test all clean

kind:
	kind create cluster --name $(KIND_CLUSTER_NAME)

kind-down:
	kind delete cluster --name $(KIND_CLUSTER_NAME)

build:
	docker build -t $(DOCKER_IMAGE):$$(git rev-parse --short HEAD) .
	kind load --name $(KIND_CLUSTER_NAME) docker-image $(DOCKER_IMAGE):$$(git rev-parse --short HEAD)

lint:
	helm lint $(HELM_CHART)

helm-install:
	kubectl create namespace $(NAMESPACE) || true
	helm install $(HELM_RELEASE) $(HELM_CHART) -n $(NAMESPACE) --set image.tag=$$(git rev-parse --short HEAD) --set timerInterval=5.0
	kubectl wait --for=create pod -l app=job-template-run --timeout=30s -n $(NAMESPACE)
	kubectl wait --for=condition=Ready pod -l app=job-template-run --timeout=30s -n $(NAMESPACE)

helm-uninstall:
	helm uninstall $(HELM_RELEASE) -n $(NAMESPACE) || true

test:
	chainsaw test tests/

all: kind build helm-install test

clean: helm-uninstall kind-down
