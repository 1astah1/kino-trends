"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";

export type Product = Schemas["Product"];
export type ProductCreate = Schemas["ProductCreate"];
export type ProductPatch = Schemas["ProductPatch"];
export type ProductStatus = Schemas["ProductStatus"];
export type ProductKind = Schemas["ProductKind"];

/** Ключи каталога продуктов (§7.4: `['products', filters]`, `['product', id]`). */
export const productKeys = {
  all: ["products"] as const,
  list: (status: ProductStatus | "all") => ["products", status] as const,
  detail: (id: string) => ["product", id] as const,
};

/** Продукты (+ `stats_7d` в списке). `status: "all"` — активные и архив. */
export function useProducts({ status = "active" }: { status?: ProductStatus | "all" } = {}) {
  return useQuery({
    queryKey: productKeys.list(status),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/products", {
          params: { query: { status: status === "all" ? undefined : status, limit: 200 } },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

export function useProduct(id: string | null | undefined) {
  return useQuery({
    queryKey: productKeys.detail(id ?? ""),
    queryFn: () => unwrap(api.GET("/api/v1/products/{product_id}", { params: { path: { product_id: id ?? "" } } })),
    enabled: !!id,
  });
}

export function useCreateProduct() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductCreate) => unwrap(api.POST("/api/v1/products", { body })),
    meta: { silentError: true },
    onSuccess: (product) => {
      queryClient.setQueryData(productKeys.detail(product.id), product);
      void queryClient.invalidateQueries({ queryKey: productKeys.all });
    },
  });
}

export function useUpdateProduct(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductPatch) =>
      unwrap(api.PATCH("/api/v1/products/{product_id}", { params: { path: { product_id: id } }, body })),
    meta: { silentError: true },
    onSuccess: (product) => {
      queryClient.setQueryData(productKeys.detail(id), product);
      void queryClient.invalidateQueries({ queryKey: productKeys.all });
    },
  });
}

export function useArchiveProduct(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/products/{product_id}/archive", { params: { path: { product_id: id } } })),
    onSuccess: (product) => {
      queryClient.setQueryData(productKeys.detail(id), product);
      void queryClient.invalidateQueries({ queryKey: productKeys.all });
    },
  });
}
