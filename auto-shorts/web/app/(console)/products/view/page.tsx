import type { Metadata } from "next";

import { PageSuspense } from "@/components/common/page-suspense";
import { ProductViewScreen } from "@/components/products/product-view-screen";

export const metadata: Metadata = { title: "Карточка продукта" };

export default function ProductViewPage() {
  return (
    <PageSuspense>
      <ProductViewScreen />
    </PageSuspense>
  );
}
