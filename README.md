# job-template-run

`job-template-run` is a Kubernetes extension designed to simplify the creation and management of Kubernetes `Job`s based on reusable templates. It provides custom resource definitions (CRDs) and a custom controller to define and execute `Job`s with minimal configuration.

## Motivation

Managing Kubernetes `Job`s can become repetitive and error-prone when defining similar `Job`s that only differ in small details, such as command-line arguments. This project aims to:

- Leverage Kubernetes `Job`s for their reliability and autoscaling capabilities.
- Enable users to define reusable `Job` templates and create `Job`s by overriding only the necessary parameters.

## Installation

To install the `job-template-run` Helm chart, use the following command:

```bash
helm repo add cellbytes https://hematoscope.github.io/job-template-run
helm repo update
helm install job-template-run cellbytes/job-template-run
```

You can customize the installation by providing a [`values.yaml`](./charts/job-template-run/values.yaml) file with the `--values` flag.

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
