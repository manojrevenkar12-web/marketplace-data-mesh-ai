// Server state through TanStack Query: one hook per resource, so screens only deal with loading, error and data.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post } from "./client";
import type { AccessRequest, Answer, Catalog, ContractFiles, DatasetDetail, Diagnosis, Evaluation, Me, Review, RunRow } from "./types";

export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/v1/me") });

export const useCatalog = (q: string) =>
  useQuery({ queryKey: ["catalog", q], queryFn: () => api<Catalog>(`/v1/catalog${q ? `?q=${encodeURIComponent(q)}` : ""}`) });

export const useDataset = (product: string) =>
  useQuery({ queryKey: ["dataset", product], queryFn: () => api<DatasetDetail>(`/v1/datasets/${encodeURIComponent(product)}`) });

export const useAsk = () => useMutation({ mutationFn: (question: string) => post<Answer>("/v1/ask", { question }) });

export const useRuns = () => useQuery({ queryKey: ["runs"], queryFn: () => api<RunRow[]>("/v1/runs") });

export const useDiagnosis = (runId: string | null) =>
  useQuery({
    queryKey: ["diagnosis", runId],
    queryFn: () => api<Diagnosis>(`/v1/runs/${encodeURIComponent(runId ?? "")}/diagnosis`),
    enabled: Boolean(runId),
  });

export const useReview = () =>
  useMutation({ mutationFn: (files: { path: string; new: string }[]) => post<Review>("/v1/reviews", { files }) });

export const useAccessRequests = () =>
  useQuery({ queryKey: ["access"], queryFn: () => api<{ mine: AccessRequest[]; to_decide: AccessRequest[] }>("/v1/access-requests") });

export function useRequestAccess() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { product: string; reason: string; days: number; sensitivities: string[] }) =>
      post<{ id: number; status: string; owner: string }>("/v1/access-requests", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["access"] }),
  });
}

export function useDecide() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision }: { id: number; decision: "approve" | "deny" }) =>
      post<{ id: number; status: string }>(`/v1/access-requests/${id}/decision`, { decision }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["access"] });
      void qc.invalidateQueries({ queryKey: ["catalog"] });
    },
  });
}

export const useEvaluation = () => useQuery({ queryKey: ["evaluation"], queryFn: () => api<Evaluation>("/v1/evaluation") });

export const useContractFiles = (product: string | null) =>
  useQuery({
    queryKey: ["contract", product],
    queryFn: () => api<ContractFiles>(`/v1/contracts/${encodeURIComponent(product ?? "")}`),
    enabled: Boolean(product),
  });

export const useFeedback = () =>
  useMutation({ mutationFn: (b: { feature: string; ref: string; helpful: boolean }) => post<{ status: string }>("/v1/feedback", b) });
