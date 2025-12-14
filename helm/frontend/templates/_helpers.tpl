{{- define "agari-frontend.name" -}}
{{- default .Chart.Name .Values.nameOverride -}}
{{- end -}}

{{- define "agari-frontend.fullname" -}}
{{- printf "%s-%s" (include "agari-frontend.name" .) .Release.Name -}}
{{- end -}}