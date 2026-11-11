"""Привязка координат начала поездки к району города (point-in-polygon).

Координаты округляются (cleaning.coord_precision), уникальных точек — десятки тысяч,
поэтому пространственный join делается один раз по справочнику точек, а не по каждой поездке.
Инкрементально: обрабатываются только точки, которых ещё нет в point_area.
"""
import logging

import pandas as pd
from shapely import STRtree, points, wkt

log = logging.getLogger(__name__)


def update_point_area(con, cfg: dict, trips_glob: str) -> int:
    p = cfg["cleaning"]["coord_precision"]
    new_points = con.execute(f"""
        select distinct round(start_lat, {p}) as lat, round(start_lng, {p}) as lng
        from read_parquet('{trips_glob}')
        where start_lat is not null and start_lng is not null
        except
        select lat, lng from point_area
    """).df()
    if new_points.empty:
        return 0

    areas = con.execute("select area_num, geometry from areas order by area_num").df()
    polys = [wkt.loads(g) for g in areas["geometry"]]
    tree = STRtree(polys)
    pts = points(new_points["lng"].to_numpy(), new_points["lat"].to_numpy())
    pt_idx, poly_idx = tree.query(pts, predicate="within")

    area = pd.Series(pd.NA, index=new_points.index, dtype="Int64")
    area.iloc[pt_idx] = areas["area_num"].to_numpy()[poly_idx]
    new_points["area_num"] = area

    con.register("pa_new", new_points)
    con.execute("insert or ignore into point_area select lat, lng, area_num from pa_new")
    con.unregister("pa_new")
    log.info("Новых точек привязано к районам: %d (вне города: %d)",
             len(new_points), int(area.isna().sum()))
    return len(new_points)
