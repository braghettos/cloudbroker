{{/*
Expand the name of the chart.
*/}}
{{- define "cloudbroker.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fullname: release-name + chart name, truncated to 63 chars.
*/}}
{{- define "cloudbroker.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "cloudbroker.labels" -}}
helm.sh/chart: {{ include "cloudbroker.chart" . }}
{{ include "cloudbroker.selectorLabels" . }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Chart label.
*/}}
{{- define "cloudbroker.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "cloudbroker.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cloudbroker.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Service account name.
*/}}
{{- define "cloudbroker.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "cloudbroker.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
App secret name: use existingSecret if set, otherwise generate from chart.
*/}}
{{- define "cloudbroker.secretName" -}}
{{- if .Values.existingSecret }}
{{- .Values.existingSecret }}
{{- else }}
{{- include "cloudbroker.fullname" . }}
{{- end }}
{{- end }}

{{/*
Ingest secret name: use ingestor.existingSecret if set, otherwise generate.
*/}}
{{- define "cloudbroker.ingestSecretName" -}}
{{- if .Values.ingestor.existingSecret }}
{{- .Values.ingestor.existingSecret }}
{{- else }}
{{- printf "%s-ingest" (include "cloudbroker.fullname" .) }}
{{- end }}
{{- end }}
