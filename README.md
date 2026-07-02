# job-template-run

`job-template-run` is a Kubernetes extension designed to simplify the creation and management of Kubernetes `Job`s based on reusable templates. It provides custom resource definitions (CRDs) and a custom controller to define and execute `Job`s with minimal configuration.

## Motivation

Managing Kubernetes `Job`s can become repetitive and error-prone when defining similar `Job`s that only differ in small details, such as command-line arguments. This project aims to:

- Leverage Kubernetes `Job`s for their reliability and autoscaling capabilities.
- Enable users to define reusable `Job` templates and create `Job`s by overriding only the necessary parameters.

## Installation

To install the `job-template-run` Helm chart, use the following command:

```bash
helm repo add cellbytes https://cellbytes.github.io/job-template-run
helm repo update
helm install job-template-run cellbytes/job-template-run
```

You can customize the installation by providing a [`values.yaml`](./charts/job-template-run/values.yaml) file with the `--values` flag.

Note that the CRDs are annotated with `helm.sh/resource-policy: keep`: uninstalling the chart leaves the `JobTemplate`/`JobRun` definitions (and therefore all your objects) in place. Delete the CRDs manually if you really want everything gone.

## Example

Suppose we have the following standard Kubernetes `Job`:

`echo-hello-world-job.yaml`

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: echo-hello-world
spec:
  template:
    spec:
      containers:
        - name: echo-container
          image: busybox
          command: ["echo"]
          args: ["Hello, world!"]
      restartPolicy: Never
```

If we want to `echo` something else besides `"Hello, world!`, we need to duplicate the whole specification. For this minimal example it isn't that unwieldy, but when you have a more complex `Job` with volumes, resource limits, failure policies etc. the duplication can easily become burdensome if not even brittle.

After applying the `CRD`s contained in this chart, we can split the `Job` into a `JobTemplate` and a `JobRun`:

`echo-job-template.yaml`

```yaml
apiVersion: cellbytes.io/v1
kind: JobTemplate
metadata:
  name: echo-template
spec:
  template:
    spec:
      containers:
        - name: echo-container
          image: busybox
          command: ["echo"]
      restartPolicy: Never
```

`echo-hello-world-job-run.yaml`

```yaml
apiVersion: cellbytes.io/v1
kind: JobRun
metadata:
  name: echo-hello-world-run
spec:
  templateRef: echo-template
  args: ["Hello, world!"]
```

Now, practically all of the job specification is contained in the `JobTemplate` resource, but creating the template doesn't yet create a Kubernetes `Job`. Instead, after creating the `JobRun` resource, the controller then creates a `Job` based on the `JobTemplate`'s `.spec`, injecting in the `JobRun`'s `.spec.args` into the templates `.spec.template.spec.containers[0].args`.

You can notice that the difference between the original `Job` and the custom `JobTemplate` is basically only the changes in `.apiVersion` and `.kind`, and removing the `args` to be later supplied in the `JobRun`.

## Custom Resource Definitions (CRDs)

### JobTemplate

The `JobTemplate` CRD allows you to define reusable templates for Kubernetes Jobs. These templates allow you to define the full Job specification ([`JobSpec`](https://kubernetes.io/docs/reference/kubernetes-api/workload-resources/job-v1/#JobSpec)) and should be referenced by `JobRun` resources.

The templates should probably represent some pre-defined types of tasks that should be versioned in some Infrastructure-as-Code framework such as [ArgoCD](https://argo-cd.readthedocs.io/en/stable/) or [Flux](https://fluxcd.io/flux/)

### JobRun

The `JobRun` CRD is used to create Jobs based on a `JobTemplate`. It allows you to override a `JobTemplate`s command or arguments, without redefining the entire Job specification. The `JobRun` inherits the status of any `Job` it causes to be created.

You most likely want to create these programmatically with some Kubernetes client library, such as the [Kubernetes Python Client](https://github.com/kubernetes-client/python).

A `JobRun` is one-shot: the controller creates exactly one `Job` for it (named `<template>-<run>`, truncated with a digest suffix when too long) and records it in `.status.jobName`. Deleting the `Job`, or letting `ttlSecondsAfterFinished` garbage-collect it, does not cause a re-run; create a new `JobRun` to run again. The created `Job` carries an owner reference to its `JobRun`, so deleting the `JobRun` cascades to the `Job` and its pods.

## Callbacks

A `JobRun` can request an HTTP notification when its `Job` reaches a terminal state:

```yaml
apiVersion: cellbytes.io/v1
kind: JobRun
metadata:
  name: echo-hello-world-run
spec:
  templateRef: echo-template
  args: ["Hello, world!"]
  callbackUrl: http://my-app.my-namespace.svc.cluster.local:8000/api/job-status
  callbackTokenSecretRef:
    name: my-callback-secret
    key: token
```

The controller POSTs `{"name": <jobrun name>, "namespace": <namespace>, "status": "Complete" | "Failed"}` to `callbackUrl`, with an `Authorization: Bearer <token>` header when a token is configured.

The token can be given either inline as `callbackToken` (visible to anyone who can read the `JobRun` or the `Job`, since it is copied to a Job annotation) or, preferably, as `callbackTokenSecretRef` referencing a `Secret` in the same namespace. The secret is read at delivery time and never leaves the controller; reading secrets requires installing the chart with `rbac.allowCallbackTokenSecrets=true`.

Delivery is at-least-once: a 2xx or 4xx response marks the callback as sent (a 4xx will not improve on retry), while 3xx/5xx responses and network errors are retried on the next reconcile tick. Retries stop once the `Job` is garbage-collected (`ttlSecondsAfterFinished`), so receivers should tolerate duplicate notifications and set a TTL in their templates to bound retries.
