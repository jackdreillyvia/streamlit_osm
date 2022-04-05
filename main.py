import logging
import operator

import geocoder
import geojson
import geopandas as gpd
import osm2geojson
import pandas
import pydeck
import streamlit as st
from osmnx.downloader import (
    _make_overpass_polygon_coord_strs,
    _make_overpass_settings,
    overpass_request,
    settings,
)
from pydeck.data_utils.viewport_helpers import compute_view
from shapely.geometry import shape
from st_aggrid import AgGrid, DataReturnMode, GridOptionsBuilder, GridUpdateMode

st.set_page_config(layout="wide")

settings.timeout = 10
settings.log_console = True
settings.log_level = logging.DEBUG


col1, col2 = st.columns(2)

with col1:
    settings.overpass_endpoint = st.selectbox(
        "Select Overpass Endpoint",
        [
            "https://overpass.kumi.systems/api/",
            "https://overpass-api.de/api/",
        ],
    )
    settings.overpass_rate_limit = (
        settings.overpass_endpoint == "https://overpass-api.de/api/"
    )
    envelope = (
        shape(
            geojson.Point(
                geocoder.mapbox(
                    st.text_input("City", "Auckland, New Zealand"),
                    key=st.secrets.get("MAPBOX_API_KEY"),
                ).latlng[::-1]
            )
        )
        .buffer(st.slider("Buffer Km", 0.5, 10.0, 3.0) / 2 / 111111.0 * 1000)
        .envelope
    )
    bounds = envelope.bounds
    bounds = [bounds[:2], bounds[2:]]
    st.pydeck_chart(
        pydeck.Deck(
            map_style="mapbox://styles/mapbox/light-v9",
            initial_view_state=compute_view(bounds),
            layers=[
                pydeck.Layer(
                    "GeoJsonLayer",
                    data=envelope.__geo_interface__,
                    pickable=True,
                    tooltip=True,
                    opacity=0.8,
                    stroked=True,
                    filled=False,
                    get_line_width=10,
                    get_line_color=[255, 0, 0],
                )
            ],
        )
    )
    if data := st.text_area(
        "Overpass Request",
        (
            (
                v := st.selectbox(
                    "Templates",
                    {
                        "Bus Stops": "node[bus=yes]",
                        "Roads": "way[highway]",
                        "Admin Boundaries": "relation[admin_level]",
                        "Rail": "way[railway]",
                    }.items(),
                    format_func=operator.itemgetter(0),
                )
            )
            and v[1]
        ),
    ):
        box = _make_overpass_polygon_coord_strs(envelope)[0]
        settings = _make_overpass_settings()
        query_data = f"""
{settings};
(
    {data}(poly:'{box}');
    >;
);
out body;
>;
out skel qt;
"""
        st.markdown(
            f"""```
    {query_data}
    ```"""
        )

        d = overpass_request(dict(data=query_data))
        if d and d.get("elements"):
            with col2:
                geojson_value = osm2geojson.json2geojson(d)
                tags = pandas.DataFrame.from_records(
                    [
                        dict(key=k, value=v)
                        for feature in geojson_value["features"]
                        for k, v in feature["properties"].get("tags", {}).items()
                        if all(f not in k for f in ("name", ":position", "wiki", "ref"))
                    ]
                ).drop_duplicates()
                builder = GridOptionsBuilder.from_dataframe(tags)
                builder.configure_selection("multiple")
                tags_grid = AgGrid(
                    tags,
                    builder.build(),
                    data_return_mode="AS_INPUT",
                    update_mode="MODEL_CHANGED",
                    fit_columns_on_grid_load=False,
                    enable_enterprise_modules=True,
                    reload_data=True,
                )
                df = gpd.GeoDataFrame.from_features(geojson_value)
                if "tags" in set(df.columns):
                    df = (
                        df[
                            df.tags.apply(
                                lambda x: isinstance(x, dict)
                                and bool(
                                    set(
                                        (g["key"], g["value"])
                                        for g in tags_grid["selected_rows"]
                                    )
                                    & set(x.items())
                                )
                            )
                        ]
                        if tags_grid["selected_rows"]
                        else df
                    )
                    df["name"] = df.tags.apply(
                        lambda x: isinstance(x, dict)
                        and x.get("name", x.get("name:en", ""))
                        or ""
                    )
                gb = GridOptionsBuilder.from_dataframe(df)
                gb.configure_pagination(paginationAutoPageSize=True)  # Add pagination
                gb.configure_side_bar()  # Add a sidebar
                gb.configure_columns(["geometry", "nodes", "tags"], hide=True)
                gb.configure_selection("multiple")
                grid_response = AgGrid(
                    df,
                    gridOptions=gb.build(),
                    data_return_mode="AS_INPUT",
                    update_mode="MODEL_CHANGED",
                    fit_columns_on_grid_load=False,
                    enable_enterprise_modules=True,
                    reload_data=True,
                )
                filtered_dataframe = (
                    gpd.GeoDataFrame.from_features(
                        geojson.FeatureCollection(
                            [
                                f
                                for f in geojson_value["features"]
                                if f["properties"].get("id")
                                in {row["id"] for row in grid_response["selected_rows"]}
                            ]
                        )
                    )
                    if grid_response["selected_rows"]
                    else df
                )
                if "tags" in filtered_dataframe.columns:
                    filtered_dataframe["name"] = filtered_dataframe.tags.apply(
                        lambda x: isinstance(x, dict)
                        and x.get("name", x.get("name:en", ""))
                        or ""
                    )
                bounds = filtered_dataframe.total_bounds
                bounds = [bounds[:2], bounds[2:]]
                st.pydeck_chart(
                    pydeck.Deck(
                        map_style="mapbox://styles/mapbox/light-v9",
                        initial_view_state=compute_view(bounds),
                        layers=[
                            pydeck.Layer(
                                "GeoJsonLayer",
                                data=filtered_dataframe,
                                pickable=True,
                                stroked=True,
                                filled=True,
                                get_line_width=10,
                                get_line_color=[255, 0, 0, 122],
                                get_fill_color=[0, 255, 0, 122],
                            )
                        ],
                        tooltip={
                            "html": "{name}",
                            "style": {"color": "white"},
                        },
                    )
                )
