# Makefile for local testing with kind, Helm, and Chainsaw

KIND_CLUSTER_NAME=job-template-run-test
NAMESPACE=job-template-run
HELM_CHART=charts/job-template-run
HELM_RELEASE=job-template-run

.PHONY: kind kind-down helm-install helm-uninstall test chainsaw-install all clean


kind:
	kind create cluster --name $(KIND_CLUSTER_NAME)

kind-down:
	kind delete cluster --name $(KIND_CLUSTER_NAME)

helm-install:
	kubectl create namespace $(NAMESPACE) || true
	helm install $(HELM_RELEASE) $(HELM_CHART) -n $(NAMESPACE)

helm-uninstall:
	helm uninstall $(HELM_RELEASE) -n $(NAMESPACE) || true

test:
	chainsaw test tests/

all: kind helm-install test

clean: helm-uninstall kind-down
