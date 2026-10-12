"""Suggest packing box tool — 3D-BPP & Hazmat Segregation (TASK-045)"""

import logging
from typing import Any

from pydantic import BaseModel, Field

try:
    from app.clients.database_client import DatabaseClient
    from app.tools.base import BaseTool, ToolResult
    from app.utils.error_handlers import handle_tool_error
    from app.utils.validators import validate_order_id
except ImportError:
    DatabaseClient = None
    class BaseTool:
        def __init__(self, config=None):
            self.config = config
    class ToolResult:
        def __init__(self, success=True, data=None, error=None, error_code=None):
            self.success = success
            self.data = data
            self.error = error
            self.error_code = error_code
    handle_tool_error = None
    validate_order_id = None

logger = logging.getLogger(__name__)


# Safety threshold limits for SIOC parcel handling (TASK-078 / DOM-03 / OH&S)
MAX_SIOC_WEIGHT_KG: float = 32.0
MAX_SIOC_DIMENSION_CM: float = 150.0


class SuggestPackingBoxInput(BaseModel):
    """Input schema for suggest_packing_box"""
    order_id: str = Field(..., description="Order ID")


class SuggestPackingBox(BaseTool):
    """Suggest optimal packing box using 3D Bin Packing Problem (3D-BPP) with dunnage buffer and hazmat segregation"""
    
    name = "suggest_packing_box"
    description = (
        "3D Bin Packing Problem (3D-BPP) optimizer: accounts for 3D item geometry, rotation constraints, "
        "10% dunnage buffer, dimensional weight (DIM weight = L*W*H/5000), and hazmat segregation."
    )
    
    def __init__(self, config=None):
        super().__init__(config)
        self.db = DatabaseClient(config) if DatabaseClient else None
    
    def get_input_schema(self) -> dict[str, Any]:
        """Get input schema"""
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "Order ID"
                }
            },
            "required": ["order_id"]
        }
    
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the 3D-BPP packing box recommendation tool"""
        try:
            input_data = SuggestPackingBoxInput(**kwargs)
            validate_order_id(input_data.order_id)
            
            await self.db.connect()
            
            # Fetch order items
            order_items = await self.db.fetch_many("SELECT order_items", input_data.order_id)
            
            if not order_items:
                doc = await self.db._grpc_client.get_document(input_data.order_id)
                if doc and doc.get("items"):
                    order_items = []
                    for item in doc["items"]:
                        prod_dim = await self.db._grpc_client.get_product(item.get("product_id", 1))
                        length = prod_dim.get("length", 20.0) if prod_dim else 20.0
                        width = prod_dim.get("width", 15.0) if prod_dim else 15.0
                        height = prod_dim.get("height", 10.0) if prod_dim else 10.0
                        weight = prod_dim.get("weight", 1.5) if prod_dim else 1.5
                        qty = item.get("quantity", 1)
                        
                        allow_rotation = prod_dim.get("allow_rotation", True) if prod_dim else True
                        hazmat_class = prod_dim.get("hazmat_class") if prod_dim else None
                        category = prod_dim.get("category", "GENERAL") if prod_dim else "GENERAL"
                        is_fragile = bool(prod_dim.get("is_fragile", False) if prod_dim else False)
                        if not is_fragile:
                            is_fragile = bool(item.get("is_fragile", False) or "FRAGILE" in str(category).upper())
                        
                        case_size = prod_dim.get("case_size", 1) if prod_dim else 1
                        uoms = prod_dim.get("uoms", []) if prod_dim else []
                        
                        order_items.append({
                            "sku_code": item.get("sku_code", f"SKU-{item.get('product_id', 1)}"),
                            "name": prod_dim.get("name", "") if prod_dim else "",
                            "quantity": qty,
                            "length": float(length),
                            "width": float(width),
                            "height": float(height),
                            "weight": float(weight),
                            "allow_rotation": bool(allow_rotation),
                            "hazmat_class": hazmat_class,
                            "category": category,
                            "is_fragile": is_fragile,
                            "case_size": int(case_size),
                            "uoms": uoms,
                            "item_volume": float(length * width * height),
                            "total_weight": float(weight * qty)
                        })
            
            if not order_items:
                await self.db.disconnect()
                return ToolResult(
                    success=False,
                    error=f"No items found for order: {input_data.order_id}",
                    error_code="NOT_FOUND"
                )
            
            # Ensure each item has necessary fields with defaults
            sanitized_items = []
            for it in order_items:
                l = float(it.get("length", 20.0))
                w = float(it.get("width", 15.0))
                h = float(it.get("height", 10.0))
                wt = float(it.get("weight", 1.0))
                q = int(it.get("quantity", 1))
                allow_rot = it.get("allow_rotation", True)
                hazmat = it.get("hazmat_class")
                cat = it.get("category", "GENERAL")
                name = it.get("name", "")
                is_frag = bool(it.get("is_fragile", False) or "FRAGILE" in str(cat).upper())
                is_liq = bool(it.get("is_liquid", False) or "LIQUID" in str(cat).upper() or "BEVERAGE" in str(cat).upper() or "OIL" in str(cat).upper() or "CHEMICAL" in str(cat).upper() or "LIQUID" in str(name).upper())
                is_elec = bool(it.get("is_electronic", False) or "ELECTRONIC" in str(cat).upper() or "DEVICE" in str(cat).upper())
                case_sz = int(it.get("case_size", 1))
                uoms_list = it.get("uoms", [])
                
                sanitized_items.append({
                    "sku_code": it.get("sku_code", "UNKNOWN"),
                    "name": name,
                    "quantity": q,
                    "length": l,
                    "width": w,
                    "height": h,
                    "weight": wt,
                    "allow_rotation": allow_rot,
                    "hazmat_class": hazmat,
                    "category": cat,
                    "is_fragile": is_frag,
                    "is_liquid": is_liq,
                    "is_electronic": is_elec,
                    "case_size": case_sz,
                    "uoms": uoms_list,
                    "item_volume": l * w * h,
                    "total_weight": wt * q
                })
            
            # 1. Automatic Recognition of SIOC Master Cartons (TASK-068 / DOM-03)
            sioc_packages, loose_items = self._partition_sioc_items(sanitized_items)
            
            # Case 1: All items are packed in master carton SIOC packages (no loose items)
            if sioc_packages and not loose_items:
                await self.db.disconnect()
                total_sioc_weight = sum(pkg["gross_weight_kg"] for pkg in sioc_packages)
                return ToolResult(
                    success=True,
                    data={
                        "order_id": input_data.order_id,
                        "order_items": sanitized_items,
                        "is_fully_sioc": True,
                        "sioc_packages": sioc_packages,
                        "loose_items": [],
                        "total_packages": len(sioc_packages),
                        "total_weight_kg": round(total_sioc_weight, 2),
                        "recommendations": [],
                        "selected_recommendation": {
                            "package_type": "FREIGHT_PALLET" if any(p.get("requires_freight_pallet") for p in sioc_packages) else "SIOC",
                            "message": (
                                f"Đơn hàng gồm kiện hàng vượt ngưỡng an toàn OH&S yêu cầu vận chuyển pallet "
                                f"({sum(1 for p in sioc_packages if p.get('requires_freight_pallet'))} kiện FREIGHT_PALLET)."
                                if any(p.get("requires_freight_pallet") for p in sioc_packages)
                                else f"Toàn bộ đơn hàng ({len(sioc_packages)} kiện) xuất nguyên thùng SIOC. Không xé lẻ đóng hộp lại."
                            ),
                            "total_sioc_packages": len(sioc_packages),
                        }
                    }
                )
            
            # Items subject to carton packing (loose items if SIOC partitioned, else all)
            items_to_pack = loose_items if loose_items else sanitized_items
            
            # 2. Check Hazmat Incompatibility Segregation
            is_compatible, conflict_msg = self._check_hazmat_segregation(items_to_pack)
            if not is_compatible:
                await self.db.disconnect()
                return ToolResult(
                    success=False,
                    error=f"Hazmat Segregation Violation: {conflict_msg}",
                    error_code="HAZMAT_INCOMPATIBILITY",
                    data={
                        "order_id": input_data.order_id,
                        "hazmat_incompatible": True,
                        "conflict_detail": conflict_msg,
                        "sioc_packages": sioc_packages
                    }
                )
            
            # Calculate aggregate statistics for items to pack in carton
            total_volume = sum(item["item_volume"] * item["quantity"] for item in items_to_pack)
            total_weight = sum(item["total_weight"] for item in items_to_pack)
            
            # Fetch candidate boxes
            available_boxes = await self._get_available_boxes()
            await self.db.disconnect()
            
            # 3. Run 3D-BPP Geometric Packing & DIM Weight Optimization
            recommendations = self._find_optimal_boxes_3d(
                items_to_pack,
                total_volume,
                total_weight,
                available_boxes,
                dunnage_buffer=0.10
            )
            
            # If no candidate box can accommodate all items, return OVERSIZED_ITEM error (TASK-062 / MED-10)
            if not recommendations:
                return ToolResult(
                    success=False,
                    error_code="OVERSIZED_ITEM",
                    error="Order contains oversized items exceeding all available carton dimensions. Palletizing / custom crate required.",
                    data={
                        "order_id": input_data.order_id,
                        "order_items": sanitized_items,
                        "sioc_packages": sioc_packages,
                        "loose_items": loose_items,
                        "total_volume_cm3": round(total_volume, 2),
                        "total_weight_kg": round(total_weight, 2),
                        "item_count": len(items_to_pack),
                        "available_box_count": len(available_boxes)
                    }
                )

            return ToolResult(
                success=True,
                data={
                    "order_id": input_data.order_id,
                    "order_items": sanitized_items,
                    "is_fully_sioc": False,
                    "sioc_packages": sioc_packages,
                    "loose_items": loose_items,
                    "total_volume_cm3": round(total_volume, 2),
                    "total_weight_kg": round(total_weight, 2),
                    "item_count": len(items_to_pack),
                    "dunnage_buffer_percent": 10.0,
                    "recommendations": recommendations,
                    "selected_recommendation": recommendations[0] if recommendations else None
                }
            )
            
        except Exception as e:  # noqa: BLE001
            return handle_tool_error(e, self.name)
    
    def _check_hazmat_segregation(self, items: list[dict[str, Any]]) -> tuple[bool, str]:
        """
        Check if any hazardous materials are mixed with food, beverage, or electronics in the same carton.
        Acceptance Criteria: Ngăn chặn việc đóng chung sản phẩm hazmat_class với thực phẩm hoặc đồ điện tử.
        """
        has_hazmat = False
        hazmat_names = []
        has_food = False
        food_names = []
        has_electronics = False
        electronics_names = []
        
        for item in items:
            h_class = item.get("hazmat_class")
            cat = str(item.get("category", "")).upper()
            name = str(item.get("name", "")).lower()
            
            # Hazmat check
            if h_class or "hazmat" in cat or "chemical" in cat or "hazmat" in name:
                has_hazmat = True
                hazmat_names.append(item.get("sku_code"))
            
            # Food check
            if "FOOD" in cat or "BEVERAGE" in cat or any(w in name for w in ["food", "beverage", "drink", "milk", "snack"]):
                has_food = True
                food_names.append(item.get("sku_code"))
                
            # Electronics check
            if "ELECTRONIC" in cat or any(w in name for w in ["electronic", "phone", "laptop", "battery", "tablet", "pc"]):
                has_electronics = True
                electronics_names.append(item.get("sku_code"))
        
        if has_hazmat and has_food:
            return False, f"Cannot co-pack Hazmat items ({', '.join(hazmat_names)}) with Food products ({', '.join(food_names)}) in the same box."
        
        if has_hazmat and has_electronics:
            return False, f"Cannot co-pack Hazmat items ({', '.join(hazmat_names)}) with Electronic products ({', '.join(electronics_names)}) in the same box."
            
        return True, ""
    
    async def _get_available_boxes(self) -> list[dict[str, Any]]:
        """Get available packing box sizes"""
        boxes = await self.db.fetch_many("SELECT packing_boxes")
        if boxes:
            return boxes
        return [
            {"box_id": 1, "name": "Small Box (S)", "length": 25.0, "width": 20.0, "height": 15.0, "max_weight": 5.0, "volume": 7500.0},
            {"box_id": 2, "name": "Medium Box (M)", "length": 40.0, "width": 30.0, "height": 25.0, "max_weight": 15.0, "volume": 30000.0},
            {"box_id": 3, "name": "Large Box (L)", "length": 60.0, "width": 40.0, "height": 35.0, "max_weight": 30.0, "volume": 84000.0},
            {"box_id": 4, "name": "Extra Large Box (XL)", "length": 80.0, "width": 60.0, "height": 50.0, "max_weight": 50.0, "volume": 240000.0},
            {"box_id": 5, "name": "Long Heavy Box (XXL)", "length": 140.0, "width": 40.0, "height": 30.0, "max_weight": 50.0, "volume": 168000.0}
        ]
    
    def _partition_sioc_items(
        self,
        items: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Partition order items into SIOC master carton packages and loose items (TASK-068 / DOM-03)"""
        return partition_sioc_items(items)

    def _find_optimal_boxes_3d(
        self,
        items: list[dict[str, Any]],
        total_volume: float,
        total_weight: float,
        available_boxes: list[dict[str, Any]],
        dunnage_buffer: float = 0.10
    ) -> list[dict[str, Any]]:
        """
        Evaluate candidate boxes using:
        1. Dunnage buffer (10% linear allowance deduction on box dimensions)
        2. 3D-BPP Geometric Packing check with rotation constraints (Extreme Points / First-Fit Decreasing 3D)
        3. Weight limit check
        4. Dimensional Weight (DIM Weight = L*W*H / 5000) & Billable Weight optimization
        5. Center of Gravity (CoG) balance optimization (TASK-067 / DOM-02)
        """
        recommendations = []
        
        for box in available_boxes:
            box_l = float(box.get("length", 0.0))
            box_w = float(box.get("width", 0.0))
            box_h = float(box.get("height", 0.0))
            max_weight = float(box.get("max_weight", 0.0))
            box_volume = float(box.get("volume", box_l * box_w * box_h))
            
            # Check weight constraint
            if max_weight < total_weight:
                continue
            
            # Usable dimensions with 10% dunnage buffer deducted
            usable_l = box_l * (1.0 - dunnage_buffer)
            usable_w = box_w * (1.0 - dunnage_buffer)
            usable_h = box_h * (1.0 - dunnage_buffer)
            usable_volume = usable_l * usable_w * usable_h
            
            if usable_volume < total_volume:
                continue
            
            # 3D Bin Packing Check with Center of Gravity optimization
            fits_in_box, placed_boxes, cog_info = self._pack_items_3d(items, usable_l, usable_w, usable_h)
            if not fits_in_box:
                continue
            
            # Calculate DIM Weight and Billable Weight
            # DIM Weight standard formula: (L * W * H) / 5000 in cm and kg
            dim_weight = (box_l * box_w * box_h) / 5000.0
            billable_weight = max(total_weight, dim_weight)
            
            wasted_space = box_volume - total_volume
            efficiency = (total_volume / box_volume) * 100.0 if box_volume > 0 else 0.0
            
            # Recommendation score: higher efficiency, lower wasted space, lower billable weight
            score = efficiency - (wasted_space / 10000.0) - (billable_weight * 0.5)
            if not cog_info.get("is_balanced", True):
                score -= 30.0  # Penalize severe CoG imbalance
            
            recommendations.append({
                "box_id": box.get("box_id"),
                "name": box.get("name"),
                "dimensions": {
                    "length": box_l,
                    "width": box_w,
                    "height": box_h
                },
                "usable_dimensions": {
                    "length": round(usable_l, 2),
                    "width": round(usable_w, 2),
                    "height": round(usable_h, 2)
                },
                "volume_cm3": box_volume,
                "usable_volume_cm3": round(usable_volume, 2),
                "max_weight_kg": max_weight,
                "gross_weight_kg": round(total_weight, 2),
                "dim_weight_kg": round(dim_weight, 2),
                "billable_weight_kg": round(billable_weight, 2),
                "efficiency_percent": round(efficiency, 2),
                "wasted_space_cm3": round(wasted_space, 2),
                "recommendation_score": round(score, 2),
                "center_of_gravity": cog_info.get("center_of_gravity"),
                "geometric_center": cog_info.get("geometric_center"),
                "is_cog_balanced": cog_info.get("is_balanced", True),
                "cog_status": cog_info.get("status", "BALANCED"),
                "cog_offset_x": cog_info.get("offset_x", 0.0),
                "cog_offset_y": cog_info.get("offset_y", 0.0),
                "cog_offset_ratio_x": cog_info.get("offset_ratio_x", 0.0),
                "cog_offset_ratio_y": cog_info.get("offset_ratio_y", 0.0),
                "placed_items_count": len(placed_boxes)
            })
        
        # Sort recommendations: lowest billable weight first, then highest efficiency
        recommendations.sort(key=lambda x: (x["billable_weight_kg"], -x["efficiency_percent"], -x["recommendation_score"]))
        return recommendations[:5]
    
    def _has_sufficient_base_support(
        self,
        item: Any,
        placement: tuple[float, float, float, float, float, float],
        placed_boxes: list[Any],
        min_ratio: float = 0.70
    ) -> bool:
        """Check base support ratio, zero-load on fragile, and liquid isolation (TASK-062 / TASK-086 / DOM-05).

        Acceptance Criteria:
        1. If pz == 0: perfectly supported by the carton bottom -> True.
        2. If pz > 0: must have at least 70% (min_ratio) of base area (dx * dy)
           supported by the top surfaces (z' + dz' == pz) of placed boxes below.
        3. Zero-Load on Fragile (TASK-086): Never place any item on top of any placed box marked is_fragile=True.
        4. Liquid Isolation (TASK-086): Never place a liquid item (is_liquid=True) on top of electronics or dry goods.
        """
        return _has_sufficient_base_support(item, placement, placed_boxes, min_ratio=min_ratio)

    def _can_pack_items_3d(
        self,
        items: list[dict[str, Any]],
        box_l: float,
        box_w: float,
        box_h: float
    ) -> bool:
        """Check if all items fit into the 3D box under rotation constraints."""
        fits, _, _ = self._pack_items_3d(items, box_l, box_w, box_h)
        return fits

    def _pack_items_3d(
        self,
        items: list[dict[str, Any]],
        box_l: float,
        box_w: float,
        box_h: float
    ) -> tuple[bool, list[Any], dict[str, Any]]:
        """
        Check if all items fit into the 3D box under rotation constraints and
        calculate/optimize Center of Gravity (CoG) (TASK-067 / DOM-02).
        Implements Extreme Points 3D placement algorithm with gravity & base support constraints.
        Returns:
            (success, placed_boxes, cog_info)
        """
        # Flatten individual unit items
        expanded_items: list[dict[str, Any]] = []
        for it in items:
            qty = max(1, int(it.get("quantity", 1)))
            cat = str(it.get("category", "")).upper()
            name = str(it.get("name", "")).upper()
            is_frag = bool(it.get("is_fragile", False) or "FRAGILE" in cat)
            is_liq = bool(it.get("is_liquid", False) or "LIQUID" in cat or "BEVERAGE" in cat or "OIL" in cat or "CHEMICAL" in cat or "LIQUID" in name)
            is_elec = bool(it.get("is_electronic", False) or "ELECTRONIC" in cat or "DEVICE" in cat)
            for _ in range(qty):
                expanded_items.append({
                    "sku_code": it.get("sku_code"),
                    "name": it.get("name", ""),
                    "length": float(it.get("length", 0.0)),
                    "width": float(it.get("width", 0.0)),
                    "height": float(it.get("height", 0.0)),
                    "weight": float(it.get("weight", 0.0)),
                    "is_fragile": is_frag,
                    "is_liquid": is_liq,
                    "is_electronic": is_elec,
                    "category": it.get("category", "GENERAL"),
                    "allow_rotation": bool(it.get("allow_rotation", True)),
                    "volume": float(it.get("length", 0.0)) * float(it.get("width", 0.0)) * float(it.get("height", 0.0))
                })
        
        # Sort items in First-Fit Decreasing order (TASK-062: sturdy non-fragile & heavier items first)
        expanded_items.sort(
            key=lambda x: (
                not x.get("is_fragile", False),
                x.get("weight", 0.0),
                x["volume"],
                max(x["length"], x["width"], x["height"])
            ),
            reverse=True
        )
        
        # Placed boxes list: [(px, py, pz, dx, dy, dz, item_dict)]
        placed_boxes: list[Any] = []
        
        # Candidate extreme points: list of (x, y, z)
        extreme_points: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0)]
        
        for item in expanded_items:
            l, w, h = item["length"], item["width"], item["height"]
            allow_rotation = item["allow_rotation"]
            
            # Determine valid orientations
            if allow_rotation:
                # 6 possible orthogonal orientations
                orientations = list({
                    (l, w, h), (l, h, w),
                    (w, l, h), (w, h, l),
                    (h, l, w), (h, w, l)
                })
            else:
                # Must stay upright: Z axis is locked to height 'h', can only rotate around Z
                orientations = list({
                    (l, w, h),
                    (w, l, h)
                })
            
            # Try to place item at the best extreme point
            placed = False
            for ep in extreme_points:
                px, py, pz = ep
                for (dx, dy, dz) in orientations:
                    # Check box boundary constraints
                    if (px + dx <= box_l + 1e-5) and (py + dy <= box_w + 1e-5) and (pz + dz <= box_h + 1e-5):
                        # Check collision/overlap with already placed boxes
                        overlap = False
                        for b in placed_boxes:
                            ox, oy, oz, odx, ody, odz = b[:6]
                            if not (
                                (px + dx <= ox + 1e-5) or (ox + odx <= px + 1e-5) or
                                (py + dy <= oy + 1e-5) or (oy + ody <= py + 1e-5) or
                                (pz + dz <= oz + 1e-5) or (oz + odz <= pz + 1e-5)
                            ):
                                overlap = True
                                break
                        
                        if overlap:
                            continue

                        # Check base support & gravity & fragile constraints (TASK-062)
                        if not self._has_sufficient_base_support(item, (px, py, pz, dx, dy, dz), placed_boxes, min_ratio=0.70):
                            continue
                        
                        # Valid placement found
                        placed_boxes.append((px, py, pz, dx, dy, dz, item))
                        placed = True
                        
                        # Generate new candidate extreme points
                        new_points = [
                            (px + dx, py, pz),
                            (px, py + dy, pz),
                            (px, py, pz + dz)
                        ]
                        
                        # Filter extreme points within box limits
                        extreme_points.remove(ep)
                        for np in new_points:
                            if np[0] < box_l and np[1] < box_w and np[2] < box_h and np not in extreme_points:
                                extreme_points.append(np)
                        
                        # Sort extreme points (prefer lower Z, then lower Y, then lower X)
                        extreme_points.sort(key=lambda p: (p[2], p[1], p[0]))
                        break
                if placed:
                    break
            
            if not placed:
                return False, [], {}
        
        # Center of Gravity Optimization: Adjust placement towards center of carton (TASK-067 / DOM-02)
        adjusted_boxes = adjust_placement_for_center_of_gravity(placed_boxes, box_l, box_w, box_h)
        cog_info = check_center_of_gravity(adjusted_boxes, box_l, box_w, box_h, max_offset_ratio=0.20)
        
        return True, adjusted_boxes, cog_info


def _has_sufficient_base_support(
    item: Any,
    placement: tuple[float, float, float, float, float, float],
    placed_boxes: list[Any],
    min_ratio: float = 0.70
) -> bool:
    """Check base support ratio, zero-load on fragile, and liquid isolation (TASK-062 / TASK-086 / DOM-05).

    1. Base Support & Gravity:
       - If pz <= 0: resting on carton bottom -> 100% support (True).
       - If pz > 0: must have at least min_ratio (70%) of base area (dx * dy)
         supported by top surfaces (z' + dz' == pz) of placed boxes below.
    2. Zero-Load on Fragile (TASK-086 / DOM-05):
       - Tuyệt đối cấm đặt bất kỳ kiện hàng nào đè lên kiện hàng có cờ is_fragile=True (xóa bỏ hoàn toàn ngưỡng 2kg).
    3. Liquid Isolation (TASK-086 / DOM-05):
       - Sản phẩm dạng lỏng (is_liquid=True) không được đặt bên trên sản phẩm điện tử / hàng khô (pz > 0).
    """
    px, py, pz, dx, dy, _dz = placement
    if pz <= 1e-5:
        return True

    base_area = dx * dy
    if base_area <= 1e-5:
        return False

    supported_area = 0.0

    # Determine if candidate item is liquid
    is_cand_liquid = False
    if isinstance(item, dict):
        cat_item = str(item.get("category", "")).upper()
        name_item = str(item.get("name", "")).upper()
        is_cand_liquid = bool(
            item.get("is_liquid", False) or
            "LIQUID" in cat_item or
            "BEVERAGE" in cat_item or
            "OIL" in cat_item or
            "CHEMICAL" in cat_item or
            "LIQUID" in name_item
        )
    elif hasattr(item, "is_liquid"):
        is_cand_liquid = bool(getattr(item, "is_liquid", False))

    for b in placed_boxes:
        if isinstance(b, (tuple, list)):
            ox, oy, oz, odx, ody, odz = b[0], b[1], b[2], b[3], b[4], b[5]
            placed_item = b[6] if len(b) > 6 else {}
        elif isinstance(b, dict):
            ox = float(b.get("x", b.get("px", 0.0)))
            oy = float(b.get("y", b.get("py", 0.0)))
            oz = float(b.get("z", b.get("pz", 0.0)))
            odx = float(b.get("dx", b.get("length", 0.0)))
            ody = float(b.get("dy", b.get("width", 0.0)))
            odz = float(b.get("dz", b.get("height", 0.0)))
            placed_item = b
        else:
            ox = float(getattr(b, "x", 0.0))
            oy = float(getattr(b, "y", 0.0))
            oz = float(getattr(b, "z", 0.0))
            odx = float(getattr(b, "dx", 0.0))
            ody = float(getattr(b, "dy", 0.0))
            odz = float(getattr(b, "dz", 0.0))
            placed_item = b

        # Check if the placed box directly touches candidate item's bottom surface
        if abs((oz + odz) - pz) <= 1e-5:
            x_overlap = max(0.0, min(px + dx, ox + odx) - max(px, ox))
            y_overlap = max(0.0, min(py + dy, oy + ody) - max(py, oy))
            overlap = x_overlap * y_overlap

            if overlap > 1e-5:
                # 1. Zero-Load on Fragile (TASK-086 / DOM-05):
                # Tuyệt đối cấm mọi kiện hàng đè lên sản phẩm có is_fragile=True (xóa bỏ hoàn toàn ngưỡng 2kg)
                is_box_fragile = False
                if isinstance(placed_item, dict):
                    is_box_fragile = bool(
                        placed_item.get("is_fragile", False) or
                        "FRAGILE" in str(placed_item.get("category", "")).upper()
                    )
                elif hasattr(placed_item, "is_fragile"):
                    is_box_fragile = bool(getattr(placed_item, "is_fragile", False))

                if is_box_fragile:
                    return False

                # 2. Liquid vs Electronics / Dry goods isolation (TASK-086 / DOM-05):
                # Nếu item hiện tại là chất lỏng (is_liquid=True) và z > 0 mà bên dưới là đồ điện tử hoặc hàng khô -> REJECT
                if is_cand_liquid:
                    is_placed_liquid = False
                    is_placed_elec_or_dry = False
                    if isinstance(placed_item, dict):
                        cat_p = str(placed_item.get("category", "")).upper()
                        name_p = str(placed_item.get("name", "")).upper()
                        is_placed_liquid = bool(
                            placed_item.get("is_liquid", False) or
                            "LIQUID" in cat_p or
                            "BEVERAGE" in cat_p or
                            "OIL" in cat_p or
                            "CHEMICAL" in cat_p or
                            "LIQUID" in name_p
                        )
                        is_placed_elec_or_dry = bool(
                            placed_item.get("is_electronic", False) or
                            placed_item.get("is_dry_goods", False) or
                            "ELECTRONIC" in cat_p or
                            "DEVICE" in cat_p or
                            "DRY" in cat_p or
                            not is_placed_liquid
                        )
                    elif hasattr(placed_item, "is_liquid"):
                        is_placed_liquid = bool(getattr(placed_item, "is_liquid", False))
                        is_placed_elec_or_dry = not is_placed_liquid

                    if is_placed_elec_or_dry:
                        return False

                supported_area += overlap

    support_ratio = supported_area / base_area
    return support_ratio + 1e-5 >= min_ratio


def calculate_center_of_gravity(
    placed_boxes: list[Any],
    box_l: float,
    box_w: float,
    box_h: float
) -> tuple[float, float, float]:
    """Calculate the 3D center of gravity (X_cg, Y_cg, Z_cg) of placed boxes within a carton (TASK-067 / DOM-02).
    
    Formula:
        x_i = px_i + dx_i / 2
        y_i = py_i + dy_i / 2
        z_i = pz_i + dz_i / 2
        
        X_cg = SUM(m_i * x_i) / SUM(m_i)
        Y_cg = SUM(m_i * y_i) / SUM(m_i)
        Z_cg = SUM(m_i * z_i) / SUM(m_i)
    """
    if not placed_boxes:
        return (round(box_l / 2.0, 2), round(box_w / 2.0, 2), round(box_h / 2.0, 2))
        
    total_mass = 0.0
    sum_mx = 0.0
    sum_my = 0.0
    sum_mz = 0.0
    
    for b in placed_boxes:
        if isinstance(b, (tuple, list)):
            px, py, pz, dx, dy, dz = float(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])
            item_data = b[6] if len(b) > 6 else {}
            weight = 0.0
            if isinstance(item_data, dict):
                weight = float(item_data.get("weight", 0.0))
            elif hasattr(item_data, "weight"):
                weight = float(getattr(item_data, "weight", 0.0))
        elif isinstance(b, dict):
            px = float(b.get("px", b.get("x", 0.0)))
            py = float(b.get("py", b.get("y", 0.0)))
            pz = float(b.get("pz", b.get("z", 0.0)))
            dx = float(b.get("dx", b.get("length", 0.0)))
            dy = float(b.get("dy", b.get("width", 0.0)))
            dz = float(b.get("dz", b.get("height", 0.0)))
            weight = float(b.get("weight", 0.0))
        else:
            px = float(getattr(b, "x", 0.0))
            py = float(getattr(b, "y", 0.0))
            pz = float(getattr(b, "z", 0.0))
            dx = float(getattr(b, "dx", 0.0))
            dy = float(getattr(b, "dy", 0.0))
            dz = float(getattr(b, "dz", 0.0))
            weight = float(getattr(b, "weight", 0.0))
            
        m_i = max(0.0, weight)
        xi = px + dx / 2.0
        yi = py + dy / 2.0
        zi = pz + dz / 2.0
        
        sum_mx += m_i * xi
        sum_my += m_i * yi
        sum_mz += m_i * zi
        total_mass += m_i
        
    if total_mass <= 1e-6:
        return (round(box_l / 2.0, 2), round(box_w / 2.0, 2), round(box_h / 2.0, 2))
        
    x_cg = sum_mx / total_mass
    y_cg = sum_my / total_mass
    z_cg = sum_mz / total_mass
    
    return (round(x_cg, 2), round(y_cg, 2), round(z_cg, 2))


def check_center_of_gravity(
    placed_boxes: list[Any],
    box_l: float,
    box_w: float,
    box_h: float,
    max_offset_ratio: float = 0.20
) -> dict[str, Any]:
    """Check if the center of gravity is within the safe threshold (<= 20% offset from carton geometric center).
    
    Acceptance Criteria (TASK-067 / DOM-02):
        |X_cg - L/2| <= 0.20 * L AND |Y_cg - W/2| <= 0.20 * W
    """
    x_cg, y_cg, z_cg = calculate_center_of_gravity(placed_boxes, box_l, box_w, box_h)
    geom_x = box_l / 2.0
    geom_y = box_w / 2.0
    geom_z = box_h / 2.0
    
    offset_x = abs(x_cg - geom_x)
    offset_y = abs(y_cg - geom_y)
    
    max_allowed_x = max_offset_ratio * box_l
    max_allowed_y = max_offset_ratio * box_w
    
    offset_ratio_x = (offset_x / box_l) if box_l > 0 else 0.0
    offset_ratio_y = (offset_y / box_w) if box_w > 0 else 0.0
    
    is_balanced = (offset_x <= max_allowed_x + 1e-5) and (offset_y <= max_allowed_y + 1e-5)
    
    return {
        "is_balanced": is_balanced,
        "center_of_gravity": (x_cg, y_cg, z_cg),
        "geometric_center": (round(geom_x, 2), round(geom_y, 2), round(geom_z, 2)),
        "offset_x": round(offset_x, 2),
        "offset_y": round(offset_y, 2),
        "offset_ratio_x": round(offset_ratio_x, 4),
        "offset_ratio_y": round(offset_ratio_y, 4),
        "max_allowed_offset_x": round(max_allowed_x, 2),
        "max_allowed_offset_y": round(max_allowed_y, 2),
        "status": "BALANCED" if is_balanced else "UNBALANCED_WARNING"
    }


def adjust_placement_for_center_of_gravity(
    placed_boxes: list[Any],
    box_l: float,
    box_w: float,
    box_h: float
) -> list[Any]:
    """Adjust item placement towards the center of the carton bottom to keep
    the center of gravity within the safe zone (+-20% of carton center).
    
    Uses rigid translation on the entire placed cluster to preserve 100% of base support
    and avoid any collisions (TASK-067 / DOM-02).
    """
    if not placed_boxes:
        return []
        
    x_cg, y_cg, _ = calculate_center_of_gravity(placed_boxes, box_l, box_w, box_h)
    
    # Find bounding box of all placed items
    min_x = float("inf")
    max_x = float("-inf")
    min_y = float("inf")
    max_y = float("-inf")
    
    extracted = []
    for b in placed_boxes:
        if isinstance(b, (tuple, list)):
            px, py, pz, dx, dy, dz = float(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])
            item_data = b[6] if len(b) > 6 else {}
            is_tuple = True
        elif isinstance(b, dict):
            px = float(b.get("px", b.get("x", 0.0)))
            py = float(b.get("py", b.get("y", 0.0)))
            pz = float(b.get("pz", b.get("z", 0.0)))
            dx = float(b.get("dx", b.get("length", 0.0)))
            dy = float(b.get("dy", b.get("width", 0.0)))
            dz = float(b.get("dz", b.get("height", 0.0)))
            item_data = b
            is_tuple = False
        else:
            px = float(getattr(b, "x", 0.0))
            py = float(getattr(b, "y", 0.0))
            pz = float(getattr(b, "z", 0.0))
            dx = float(getattr(b, "dx", 0.0))
            dy = float(getattr(b, "dy", 0.0))
            dz = float(getattr(b, "dz", 0.0))
            item_data = b
            is_tuple = False
            
        min_x = min(min_x, px)
        max_x = max(max_x, px + dx)
        min_y = min(min_y, py)
        max_y = max(max_y, py + dy)
        extracted.append((is_tuple, px, py, pz, dx, dy, dz, item_data))
        
    # Desired shift to align CoG with geometric center (box_l / 2, box_w / 2)
    target_shift_x = (box_l / 2.0) - x_cg
    target_shift_y = (box_w / 2.0) - y_cg
    
    # Boundary clamp: cluster must stay completely within [0, box_l] and [0, box_w]
    shift_x = max(-min_x, min(box_l - max_x, target_shift_x))
    shift_y = max(-min_y, min(box_w - max_y, target_shift_y))
    
    adjusted_boxes = []
    for is_tuple, px, py, pz, dx, dy, dz, item_data in extracted:
        new_px = round(px + shift_x, 2)
        new_py = round(py + shift_y, 2)
        if is_tuple:
            adjusted_boxes.append((new_px, new_py, pz, dx, dy, dz, item_data))
        else:
            if isinstance(item_data, dict):
                new_dict = dict(item_data)
                if "px" in new_dict:
                    new_dict["px"] = new_px
                if "x" in new_dict:
                    new_dict["x"] = new_px
                if "py" in new_dict:
                    new_dict["py"] = new_py
                if "y" in new_dict:
                    new_dict["y"] = new_py
                adjusted_boxes.append(new_dict)
            else:
                setattr(item_data, "x", new_px)
                setattr(item_data, "y", new_py)
                adjusted_boxes.append(item_data)
                
    return adjusted_boxes


def partition_sioc_items(
    items: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Partition order items into master carton SIOC packages and loose items.
    
    Formula (Acceptance Criteria TASK-068 / DOM-03):
        Cases = Q // Case Size
        Loose = Q % Case Size
        
    Args:
        items: List of item dictionaries with quantity, case_size (or uoms), weight, dimensions.
        
    Returns:
        (sioc_packages, loose_items)
    """
    sioc_packages: list[dict[str, Any]] = []
    loose_items: list[dict[str, Any]] = []
    
    for it in items:
        sku = it.get("sku_code", "UNKNOWN")
        name = it.get("name", "")
        qty = int(it.get("quantity", 1))
        
        # Detect case size from explicit field or UOM conversions
        case_size = int(it.get("case_size", 1))
        if case_size <= 1 and it.get("uoms"):
            for u in it["uoms"]:
                u_code = str(u.get("uom_code", "")).upper()
                c_factor = float(u.get("conversion_factor", 1.0))
                if u_code in ["CASE", "CS", "CARTON", "BOX"] and c_factor > 1:
                    case_size = int(c_factor)
                    break
        
        # Check if eligible for case packaging (has valid case packaging and qty >= case_size)
        if case_size > 1 and qty >= case_size:
            cases_count = qty // case_size
            loose_count = qty % case_size
            
            unit_weight = float(it.get("weight", 0.0))
            case_weight = float(it.get("case_weight", round(unit_weight * case_size, 2)))
            
            l = float(it.get("length", 0.0))
            w = float(it.get("width", 0.0))
            h = float(it.get("height", 0.0))
            case_l = float(it.get("case_length", l))
            case_w = float(it.get("case_width", w))
            case_h = float(it.get("case_height", h))
            max_dim = max(case_l, case_w, case_h)
            
            # TASK-078: Check safety weight (<= 32.0kg) and max dimension (<= 150.0cm) [DOM-03]
            is_overweight = case_weight > MAX_SIOC_WEIGHT_KG
            is_oversized = max_dim > MAX_SIOC_DIMENSION_CM
            requires_freight = is_overweight or is_oversized
            
            for c_idx in range(cases_count):
                if requires_freight:
                    sioc_packages.append({
                        "package_id": f"FREIGHT-{sku}-{c_idx + 1}",
                        "package_type": "FREIGHT_PALLET",
                        "is_sioc": False,
                        "requires_freight_pallet": True,
                        "shipping_method": "FREIGHT_PALLET",
                        "sku_code": sku,
                        "name": name,
                        "case_index": c_idx + 1,
                        "total_cases": cases_count,
                        "quantity_per_case": case_size,
                        "gross_weight_kg": case_weight,
                        "max_dimension_cm": max_dim,
                        "shipping_label": f"FREIGHT-PALLET-{sku}-{c_idx + 1}",
                        "instructions": (
                            f"Kiện hàng vượt ngưỡng bưu kiện thông thường (khối lượng {case_weight}kg > {MAX_SIOC_WEIGHT_KG}kg "
                            f"hoặc kích thước {max_dim}cm > {MAX_SIOC_DIMENSION_CM}cm). Bắt buộc đóng pallet và vận chuyển hàng nặng (FREIGHT_PALLET)."
                        ),
                    })
                else:
                    sioc_packages.append({
                        "package_id": f"SIOC-{sku}-{c_idx + 1}",
                        "package_type": "SIOC",
                        "is_sioc": True,
                        "requires_freight_pallet": False,
                        "shipping_method": "PARCEL",
                        "sku_code": sku,
                        "name": name,
                        "case_index": c_idx + 1,
                        "total_cases": cases_count,
                        "quantity_per_case": case_size,
                        "gross_weight_kg": case_weight,
                        "max_dimension_cm": max_dim,
                        "shipping_label": f"SHIP-SIOC-{sku}-{c_idx + 1}",
                        "instructions": "Dán nhãn vận chuyển trực tiếp lên thùng nguyên seal của nhà sản xuất (SIOC). Không đóng gói lại.",
                    })
                
            if loose_count > 0:
                loose_item = dict(it)
                loose_item["quantity"] = loose_count
                loose_item["total_weight"] = round(unit_weight * loose_count, 2)
                loose_items.append(loose_item)
        else:
            loose_items.append(dict(it))
            
    return sioc_packages, loose_items

