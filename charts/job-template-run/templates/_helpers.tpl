{{- define "job-template-run.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "job-template-run.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s" (include "job-template-run.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "job-template-run.serviceAccountName" -}}
{{- if .Values.serviceAccount.name }}
{{- .Values.serviceAccount.name }}
{{- else }}
{{- include "job-template-run.fullname" . }}
{{- end }}
{{- end }}
