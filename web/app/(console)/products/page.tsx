import type { Metadata } from "next";

import { PageSuspense } from "@/components/common/page-suspense";
import { ProductsScreen } from "@/components/products/products-screen";

export const metadata: Metadata = { title: "Продукты и баннеры" };

export default function ProductsPage() {
  return (
    <PageSuspense>
      <ProductsScreen />
    </PageSuspense>
  );
}
