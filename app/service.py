import asyncio
from datetime import datetime
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from app.client import UrjaNotFoundError, UrjaPortalClient
from app.config import settings
from app.schemas import (
    DistributionTransformer,
    DTDetail,
    EnergyReading,
    EnergySummary,
    FleetStats,
    GeoLocation,
    HierarchyLevel,
    HierarchyLevelSummaryItem,
    HierarchySummary,
    HierarchyTreeNode,
    MeterDetail,
    MeterEnergyResponse,
    MeterHierarchy,
    MeterNearbyItem,
    MeterSummary,
    PaginatedResponse,
)

logger = logging.getLogger(__name__)


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance in kilometers between two points on Earth."""
    r = 6371.0  # Earth's mean radius in km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


def parse_urja_timestamp(ts_str: str) -> Optional[str]:
    """Parse 'DD/MM/YYYY HH:MM' into ISO-8601 string 'YYYY-MM-DDTHH:MM:SS'."""
    try:
        dt = datetime.strptime(ts_str.strip(), "%d/%m/%Y %H:%M")
        return dt.isoformat()
    except Exception:
        return None


class MeterService:
    """
    Core domain service that reconciles, normalizes, caches, and indexes data
    from the Urja Meter Ops portal, providing fast queries, hierarchy reconstruction,
    geospatial searches, and energy analytics.
    """

    def __init__(self, client: Optional[UrjaPortalClient] = None):
        self.client = client or UrjaPortalClient()
        self.cache_ttl = settings.cache_ttl_seconds

        # In-memory cached entities
        self._meters_cache: Dict[str, MeterDetail] = {}
        self._dts_cache: Dict[str, DistributionTransformer] = {}
        self._energy_cache: Dict[str, Tuple[float, MeterEnergyResponse]] = {}
        self._last_synced_at: Optional[float] = None
        self._sync_lock: Optional[asyncio.Lock] = None
        self._sync_lock_loop: Optional[asyncio.AbstractEventLoop] = None

        # Canonical maps learned from dataset to heal 22 hierarchy inconsistencies
        self._canonical_name_to_code: Dict[Tuple[str, str], str] = {}
        self._canonical_code_to_name: Dict[Tuple[str, str], str] = {}

    @property
    def sync_lock(self) -> asyncio.Lock:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._sync_lock is None or self._sync_lock_loop != current_loop:
            self._sync_lock = asyncio.Lock()
            self._sync_lock_loop = current_loop
        return self._sync_lock

    @property
    def is_cache_valid(self) -> bool:
        if not self._last_synced_at or not self._meters_cache:
            return False
        return (time.time() - self._last_synced_at) < self.cache_ttl

    @property
    def last_synced_iso(self) -> Optional[str]:
        if not self._last_synced_at:
            return None
        return datetime.fromtimestamp(self._last_synced_at).isoformat()

    async def ensure_synced(self, force: bool = False):
        """Ensure local dataset is populated and fresh within TTL."""
        if self.is_cache_valid and not force:
            return

        async with self.sync_lock:
            if self.is_cache_valid and not force:
                return
            await self._sync_from_portal()

    async def _sync_from_portal(self):
        """Fetch bulk export & DTs from portal, reconcile hierarchy, and build indexes."""
        logger.info("Syncing meter data from Urja portal...")
        start_time = time.time()

        # 1. Fetch DT list and bulk export concurrently
        dt_task = self.client.get_all_dts()
        export_task = self.client.get_bulk_export()
        dts_raw, export_raw = await asyncio.gather(dt_task, export_task)

        raw_meters = export_raw.get("data", [])
        logger.info(f"Retrieved {len(raw_meters)} meters and {len(dts_raw)} DTs from portal.")

        # 2. Build DT cache
        dts_map: Dict[str, DistributionTransformer] = {}
        for d in dts_raw:
            code = d.get("code", "")
            dts_map[code] = DistributionTransformer(
                code=code,
                name=d.get("name", ""),
                feeder_code=d.get("feederCode", ""),
                capacity_kva=float(d.get("capacityKva", 0)),
                meter_count=0,
            )

        # 3. Learn canonical (level, name) <-> code mappings across valid meters
        hierarchy_levels = ["zone", "circle", "division", "subdivision", "substation", "feeder", "dt"]
        for m in raw_meters:
            h = m.get("hierarchy", {})
            for level in hierarchy_levels:
                obj = h.get(level)
                if obj and isinstance(obj, dict):
                    name = (obj.get("name") or "").strip()
                    code = (obj.get("code") or "").strip()
                    if name and code:
                        self._canonical_name_to_code[(level, name)] = code
                        self._canonical_code_to_name[(level, code)] = name

        # Supplement feeder/dt canonicals from DT list
        for d in dts_raw:
            if d.get("feederCode"):
                f_code = d["feederCode"]
                f_num = f_code.replace("F-0", "").replace("F-", "")
                self._canonical_name_to_code[("feeder", f"Feeder {int(f_num)}")] = f_code
                self._canonical_code_to_name[("feeder", f_code)] = f"Feeder {int(f_num)}"
            if d.get("code") and d.get("name"):
                self._canonical_name_to_code[("dt", d["name"])] = d["code"]
                self._canonical_code_to_name[("dt", d["code"])] = d["name"]

        # 4. Normalize and heal meter records
        meters_map: Dict[str, MeterDetail] = {}
        for m in raw_meters:
            meter_id = m.get("meterId")
            h_raw = m.get("hierarchy", {})

            # Reconcile each hierarchy level
            healed_hierarchy = {}
            for level in hierarchy_levels:
                obj = h_raw.get(level) or {}
                name = (obj.get("name") or "").strip()
                code = (obj.get("code") or "").strip()

                # Self-healing missing codes or missing names
                if name and not code:
                    code = self._canonical_name_to_code.get((level, name), "")
                elif code and not name:
                    name = self._canonical_code_to_name.get((level, code), "")

                if code or name:
                    healed_hierarchy[level] = HierarchyLevel(code=code, name=name)

            dt_code = m.get("dtCode") or (healed_hierarchy.get("dt").code if "dt" in healed_hierarchy else None)
            feeder_code = (
                healed_hierarchy.get("feeder").code
                if "feeder" in healed_hierarchy
                else (dts_map.get(dt_code).feeder_code if dt_code in dts_map else None)
            )

            # Geographic coordinates
            geo_obj = None
            if "geo" in m and m["geo"]:
                lat = float(m["geo"].get("lat", 0))
                lng = float(m["geo"].get("lng", 0))
                if lat != 0 or lng != 0:
                    geo_obj = GeoLocation(latitude=lat, longitude=lng)

            meter_detail = MeterDetail(
                meter_id=meter_id,
                serial_no=m.get("serialNo", ""),
                make=m.get("make", ""),
                phase_type=m.get("phaseType", ""),
                install_status=m.get("installStatus", ""),
                install_type=m.get("installType"),
                build=m.get("build"),
                dt_code=dt_code,
                feeder_code=feeder_code,
                geo=geo_obj,
                hierarchy=MeterHierarchy(**healed_hierarchy),
                extra_parameters={
                    "build": m.get("build"),
                    "installType": m.get("installType"),
                },
            )
            meters_map[meter_id] = meter_detail

            # Increment meter count for DT
            if dt_code and dt_code in dts_map:
                dts_map[dt_code].meter_count += 1

        self._meters_cache = meters_map
        self._dts_cache = dts_map
        self._last_synced_at = time.time()
        logger.info(f"Sync complete in {time.time() - start_time:.2f}s. Indexed {len(meters_map)} meters.")

    async def list_meters(
        self,
        q: Optional[str] = None,
        make: Optional[str] = None,
        phase_type: Optional[str] = None,
        install_status: Optional[str] = None,
        install_type: Optional[str] = None,
        build: Optional[str] = None,
        dt_code: Optional[str] = None,
        feeder_code: Optional[str] = None,
        substation_code: Optional[str] = None,
        sort_by: str = "meter_id",
        order: str = "asc",
        page: int = 1,
        limit: int = 20,
    ) -> PaginatedResponse[MeterSummary]:
        """
        Query meters with rich multi-attribute filtering, sorting, and pagination.
        Solves the portal's search limitations.
        """
        await self.ensure_synced()

        filtered: List[MeterDetail] = list(self._meters_cache.values())

        if q:
            q_lower = q.strip().lower()
            filtered = [
                m for m in filtered
                if q_lower in m.meter_id.lower() or q_lower in m.serial_no.lower()
            ]

        if make:
            make_lower = make.strip().lower()
            filtered = [m for m in filtered if m.make.lower() == make_lower]

        if phase_type:
            phase_lower = phase_type.strip().lower()
            filtered = [m for m in filtered if m.phase_type.lower() == phase_lower]

        if install_status:
            status_lower = install_status.strip().lower()
            filtered = [m for m in filtered if m.install_status.lower() == status_lower]

        if install_type:
            it_lower = install_type.strip().lower()
            filtered = [m for m in filtered if m.install_type and m.install_type.lower() == it_lower]

        if build:
            build_lower = build.strip().lower()
            filtered = [m for m in filtered if m.build and m.build.lower() == build_lower]

        if dt_code:
            dt_upper = dt_code.strip().upper()
            filtered = [m for m in filtered if m.dt_code and m.dt_code.upper() == dt_upper]

        if feeder_code:
            feeder_upper = feeder_code.strip().upper()
            filtered = [m for m in filtered if m.feeder_code and m.feeder_code.upper() == feeder_upper]

        if substation_code:
            sub_upper = substation_code.strip().upper()
            filtered = [
                m for m in filtered
                if m.hierarchy.substation and m.hierarchy.substation.code.upper() == sub_upper
            ]

        # Sorting
        reverse = order.lower() == "desc"
        if sort_by in ("meter_id", "serial_no", "make", "phase_type", "install_status", "dt_code", "feeder_code"):
            filtered.sort(key=lambda m: getattr(m, sort_by) or "", reverse=reverse)
        else:
            filtered.sort(key=lambda m: m.meter_id, reverse=reverse)

        total = len(filtered)
        limit = max(1, min(limit, 200))
        page = max(1, page)
        total_pages = max(1, math.ceil(total / limit))
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit

        data = [
            MeterSummary(
                meter_id=m.meter_id,
                serial_no=m.serial_no,
                make=m.make,
                phase_type=m.phase_type,
                install_status=m.install_status,
                install_type=m.install_type,
                build=m.build,
                dt_code=m.dt_code,
                feeder_code=m.feeder_code,
                geo=m.geo,
            )
            for m in filtered[start_idx:end_idx]
        ]

        return PaginatedResponse(
            data=data,
            total=total,
            page=page,
            limit=limit,
            total_pages=total_pages,
        )

    async def get_meter_by_id(self, meter_id: str) -> MeterDetail:
        """Fetch detailed meter information, incorporating nameplate and hierarchy."""
        await self.ensure_synced()
        normalized_id = meter_id.strip().upper()

        if normalized_id in self._meters_cache:
            meter = self._meters_cache[normalized_id]
            # If coordinates are missing, attempt on-demand geo lookup
            if not meter.geo:
                geo = await self.client.get_meter_geo(meter.meter_id)
                if geo:
                    meter.geo = GeoLocation(**geo)
            return meter

        # If not in cache, try direct lookup from portal detail page
        try:
            ssr_data = await self.client.get_meter_page_details(normalized_id)
            geo_data = await self.client.get_meter_geo(normalized_id)
            geo_obj = GeoLocation(**geo_data) if geo_data else None

            # Parse parameters from detail
            detail_items = ssr_data.get("detail", {})
            params: Dict[str, Any] = {}
            if "classData" in detail_items:
                import json
                try:
                    cdata = json.loads(detail_items["classData"]).get("installed_meter", {})
                    params = {k.lower(): str(v) for k, v in cdata.items()}
                except Exception:
                    pass
            elif "data" in detail_items:
                for item in detail_items["data"]:
                    name = item.get("parameterName", "").lower().replace(" ", "_")
                    params[name] = item.get("parameterValue")

            meter = MeterDetail(
                meter_id=normalized_id,
                serial_no=params.get("serial_no") or params.get("serialno") or "",
                make=params.get("make") or "Unknown",
                phase_type=params.get("phase_type") or params.get("phasetype") or "single",
                install_status=params.get("installation_status") or params.get("installationstatus") or "Installed",
                install_type=params.get("installation_type") or params.get("installationtype"),
                build="v2" if "classData" in detail_items else "legacy",
                dt_code=None,
                feeder_code=None,
                geo=geo_obj,
                extra_parameters=params,
            )
            self._meters_cache[normalized_id] = meter
            return meter
        except UrjaNotFoundError:
            raise UrjaNotFoundError(f"Meter '{meter_id}' not found.")

    async def get_meters_nearby(
        self,
        latitude: float,
        longitude: float,
        radius_km: float = 5.0,
        limit: int = 50,
    ) -> List[MeterNearbyItem]:
        """
        Geospatial proximity query: find meters within a given radius in kilometers.
        Calculates Haversine distances and returns items sorted nearest to furthest.
        """
        await self.ensure_synced()

        nearby: List[Tuple[float, MeterDetail]] = []
        for meter in self._meters_cache.values():
            if meter.geo:
                dist = haversine_distance_km(latitude, longitude, meter.geo.latitude, meter.geo.longitude)
                if dist <= radius_km:
                    nearby.append((dist, meter))

        nearby.sort(key=lambda x: x[0])
        limit = max(1, min(limit, 200))

        return [
            MeterNearbyItem(
                meter_id=m.meter_id,
                serial_no=m.serial_no,
                make=m.make,
                phase_type=m.phase_type,
                install_status=m.install_status,
                install_type=m.install_type,
                build=m.build,
                dt_code=m.dt_code,
                feeder_code=m.feeder_code,
                geo=m.geo,
                distance_km=round(d, 3),
            )
            for d, m in nearby[:limit]
        ]

    async def get_meter_energy(
        self,
        meter_id: str,
        include_summary: bool = True,
        limit: Optional[int] = None,
    ) -> MeterEnergyResponse:
        """
        Fetch and parse 30-minute interval readings for a meter.
        Computes summary statistics: total active energy consumed, voltage bounds,
        average voltage, and estimated power factor.
        """
        normalized_id = meter_id.strip().upper()
        now = time.time()

        # Check short-term energy cache (60 seconds)
        if normalized_id in self._energy_cache:
            cached_time, cached_res = self._energy_cache[normalized_id]
            if now - cached_time < 60:
                return cached_res

        # Fetch from portal
        raw_readings = await self.client.get_meter_energy(normalized_id)
        if not raw_readings:
            # Check if meter exists
            await self.get_meter_by_id(normalized_id)
            return MeterEnergyResponse(meter_id=normalized_id, summary=None, readings=[])

        parsed_readings: List[EnergyReading] = []
        voltages: List[float] = []

        for r in raw_readings:
            ts = r.get("timestamp", "")
            iso_ts = parse_urja_timestamp(ts)
            kwh = float(r.get("kwh", 0))
            kvah = float(r.get("kvah", 0))
            volt_r = float(r.get("voltR", 0))
            voltages.append(volt_r)

            parsed_readings.append(
                EnergyReading(
                    timestamp=ts,
                    timestamp_iso=iso_ts,
                    kwh=kwh,
                    kvah=kvah,
                    volt_r=volt_r,
                )
            )

        summary = None
        if include_summary and parsed_readings:
            first_r = parsed_readings[0]
            last_r = parsed_readings[-1]
            net_kwh = round(last_r.kwh - first_r.kwh, 3)
            net_kvah = round(last_r.kvah - first_r.kvah, 3)
            pf = round(net_kwh / net_kvah, 3) if net_kvah > 0 and net_kwh >= 0 else None

            summary = EnergySummary(
                reading_count=len(parsed_readings),
                first_timestamp=first_r.timestamp,
                last_timestamp=last_r.timestamp,
                start_kwh=first_r.kwh,
                end_kwh=last_r.kwh,
                net_kwh_consumed=net_kwh,
                avg_voltage_r=round(sum(voltages) / len(voltages), 1) if voltages else None,
                min_voltage_r=min(voltages) if voltages else None,
                max_voltage_r=max(voltages) if voltages else None,
                estimated_power_factor=pf,
            )

        if limit and limit > 0:
            parsed_readings = parsed_readings[:limit]

        response = MeterEnergyResponse(
            meter_id=normalized_id,
            summary=summary,
            readings=parsed_readings,
        )

        self._energy_cache[normalized_id] = (now, response)
        return response

    async def get_transformers(self) -> List[DistributionTransformer]:
        """Return all distribution transformers with accurate meter counts."""
        await self.ensure_synced()
        return list(self._dts_cache.values())

    async def get_transformer_detail(self, dt_code: str) -> DTDetail:
        """Return detail of a distribution transformer and its connected meters."""
        await self.ensure_synced()
        code_upper = dt_code.strip().upper()
        if code_upper not in self._dts_cache:
            raise UrjaNotFoundError(f"Distribution transformer '{dt_code}' not found.")

        dt = self._dts_cache[code_upper]
        meters = [
            MeterSummary(
                meter_id=m.meter_id,
                serial_no=m.serial_no,
                make=m.make,
                phase_type=m.phase_type,
                install_status=m.install_status,
                install_type=m.install_type,
                build=m.build,
                dt_code=m.dt_code,
                feeder_code=m.feeder_code,
                geo=m.geo,
            )
            for m in self._meters_cache.values()
            if m.dt_code and m.dt_code.upper() == code_upper
        ]

        return DTDetail(
            code=dt.code,
            name=dt.name,
            feeder_code=dt.feeder_code,
            capacity_kva=dt.capacity_kva,
            meter_count=len(meters),
            meters=meters,
        )

    async def get_hierarchy_tree(self) -> HierarchyTreeNode:
        """
        Reconstruct the full electrical network tree:
        Discom -> Zone -> Circle -> Division -> Subdivision -> Substation -> Feeder -> DT.
        Calculates aggregate meter counts at every level.
        """
        await self.ensure_synced()

        tree_root = HierarchyTreeNode(
            level="discom",
            code="URJA-DISCOM",
            name="Urja Distribution Utility",
            meter_count=len(self._meters_cache),
            children=[],
        )

        # Build nested dictionary structure:
        # zone -> circle -> division -> subdivision -> substation -> feeder -> dt
        tree_map: Dict[str, Any] = {}

        for meter in self._meters_cache.values():
            h = meter.hierarchy
            z = h.zone.code if h.zone else "UNKNOWN_Z"
            z_name = h.zone.name if h.zone else "Unknown Zone"
            c = h.circle.code if h.circle else "UNKNOWN_C"
            c_name = h.circle.name if h.circle else "Unknown Circle"
            d = h.division.code if h.division else "UNKNOWN_D"
            d_name = h.division.name if h.division else "Unknown Division"
            sd = h.subdivision.code if h.subdivision else "UNKNOWN_SD"
            sd_name = h.subdivision.name if h.subdivision else "Unknown Subdivision"
            ss = h.substation.code if h.substation else "UNKNOWN_SS"
            ss_name = h.substation.name if h.substation else "Unknown Substation"
            f = h.feeder.code if h.feeder else "UNKNOWN_F"
            f_name = h.feeder.name if h.feeder else "Unknown Feeder"
            dt_code = meter.dt_code or "UNKNOWN_DT"
            dt_name = self._dts_cache[dt_code].name if dt_code in self._dts_cache else dt_code

            # Navigate / build branches
            lvl_z = tree_map.setdefault(z, {"name": z_name, "count": 0, "children": {}})
            lvl_z["count"] += 1

            lvl_c = lvl_z["children"].setdefault(c, {"name": c_name, "count": 0, "children": {}})
            lvl_c["count"] += 1

            lvl_d = lvl_c["children"].setdefault(d, {"name": d_name, "count": 0, "children": {}})
            lvl_d["count"] += 1

            lvl_sd = lvl_d["children"].setdefault(sd, {"name": sd_name, "count": 0, "children": {}})
            lvl_sd["count"] += 1

            lvl_ss = lvl_sd["children"].setdefault(ss, {"name": ss_name, "count": 0, "children": {}})
            lvl_ss["count"] += 1

            lvl_f = lvl_ss["children"].setdefault(f, {"name": f_name, "count": 0, "children": {}})
            lvl_f["count"] += 1

            lvl_dt = lvl_f["children"].setdefault(dt_code, {"name": dt_name, "count": 0, "children": {}})
            lvl_dt["count"] += 1

        # Recursive converter to HierarchyTreeNode
        def build_nodes(level_name: str, child_level_name: Optional[str], mapping: Dict[str, Any]) -> List[HierarchyTreeNode]:
            nodes = []
            for code, data in sorted(mapping.items()):
                children = (
                    build_nodes(child_level_name, next_level(child_level_name), data["children"])
                    if child_level_name and data.get("children")
                    else []
                )
                nodes.append(
                    HierarchyTreeNode(
                        level=level_name,
                        code=code,
                        name=data["name"],
                        meter_count=data["count"],
                        children=children,
                    )
                )
            return nodes

        def next_level(current: Optional[str]) -> Optional[str]:
            levels = ["zone", "circle", "division", "subdivision", "substation", "feeder", "dt"]
            if not current or current not in levels:
                return None
            idx = levels.index(current)
            return levels[idx + 1] if idx + 1 < len(levels) else None

        tree_root.children = build_nodes("zone", "circle", tree_map)
        return tree_root

    async def get_hierarchy_summary(self) -> HierarchySummary:
        """Return categorized lists of distinct entities at every level with meter counts."""
        await self.ensure_synced()

        counts: Dict[str, Dict[str, Dict[str, Any]]] = {
            "zones": {},
            "circles": {},
            "divisions": {},
            "subdivisions": {},
            "substations": {},
            "feeders": {},
            "dts": {},
        }

        for m in self._meters_cache.values():
            h = m.hierarchy
            if h.zone and h.zone.code:
                z = counts["zones"].setdefault(h.zone.code, {"name": h.zone.name, "count": 0})
                z["count"] += 1
            if h.circle and h.circle.code:
                c = counts["circles"].setdefault(h.circle.code, {"name": h.circle.name, "count": 0})
                c["count"] += 1
            if h.division and h.division.code:
                d = counts["divisions"].setdefault(h.division.code, {"name": h.division.name, "count": 0})
                d["count"] += 1
            if h.subdivision and h.subdivision.code:
                sd = counts["subdivisions"].setdefault(h.subdivision.code, {"name": h.subdivision.name, "count": 0})
                sd["count"] += 1
            if h.substation and h.substation.code:
                ss = counts["substations"].setdefault(h.substation.code, {"name": h.substation.name, "count": 0})
                ss["count"] += 1
            if h.feeder and h.feeder.code:
                f = counts["feeders"].setdefault(h.feeder.code, {"name": h.feeder.name, "count": 0})
                f["count"] += 1
            if m.dt_code:
                dt_name = self._dts_cache[m.dt_code].name if m.dt_code in self._dts_cache else m.dt_code
                dt_item = counts["dts"].setdefault(m.dt_code, {"name": dt_name, "count": 0})
                dt_item["count"] += 1

        def to_items(d: Dict[str, Dict[str, Any]]) -> List[HierarchyLevelSummaryItem]:
            return [
                HierarchyLevelSummaryItem(code=code, name=info["name"], meter_count=info["count"])
                for code, info in sorted(d.items())
            ]

        return HierarchySummary(
            zones=to_items(counts["zones"]),
            circles=to_items(counts["circles"]),
            divisions=to_items(counts["divisions"]),
            subdivisions=to_items(counts["subdivisions"]),
            substations=to_items(counts["substations"]),
            feeders=to_items(counts["feeders"]),
            dts=to_items(counts["dts"]),
        )

    async def get_fleet_stats(self) -> FleetStats:
        """Aggregate fleet-wide operational metrics."""
        await self.ensure_synced()

        by_status: Dict[str, int] = {}
        by_make: Dict[str, int] = {}
        by_phase: Dict[str, int] = {}
        by_build: Dict[str, int] = {}

        feeders: set = set()
        substations: set = set()

        for m in self._meters_cache.values():
            by_status[m.install_status] = by_status.get(m.install_status, 0) + 1
            by_make[m.make] = by_make.get(m.make, 0) + 1
            by_phase[m.phase_type] = by_phase.get(m.phase_type, 0) + 1
            if m.build:
                by_build[m.build] = by_build.get(m.build, 0) + 1
            if m.feeder_code:
                feeders.add(m.feeder_code)
            if m.hierarchy.substation and m.hierarchy.substation.code:
                substations.add(m.hierarchy.substation.code)

        total_cap = sum(dt.capacity_kva for dt in self._dts_cache.values())

        return FleetStats(
            total_meters=len(self._meters_cache),
            by_status=by_status,
            by_make=by_make,
            by_phase=by_phase,
            by_build=by_build,
            total_dts=len(self._dts_cache),
            total_feeders=len(feeders),
            total_substations=len(substations),
            total_transformer_capacity_kva=round(total_cap, 1),
        )
