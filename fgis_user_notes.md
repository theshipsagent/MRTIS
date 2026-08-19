

FGIS instructions

create seperate table

here is source, all years are there https://fgisonline.ams.usda.gov/ExportGrainReport/default.aspx

first column "Thursday" equal date of the report, they update everythursday, so would want to run script to pull latest every friday, but we can add that later, or a toggle to pull in latest data ?

keep all colums in tables

out put tables only need

Thursday	Type Shipm	Cert Date	Type Carrier	Carrier Name	Grain	Class	Pounds	Destination	Field Office	Port	AMS Reg	FGIS Reg	Metric Ton

to filter what we need only for output (all other data can remain) filter on Typer Carrier = 1 and Port = MISSISSIPPI R.

the Cert Date = date of sailing from a grain elevator, note this report can only match to a sailing from a grain elevator

recommend search in sequence, month at a time, so lighten the load on the script, just plan for month rollovers eg a vessel arrried 01/31 and sailed 02/03, but this wil be obviousm as its in time sequencem 

matching sailed in mrtis will match cert date almost always except where the certs were issued on 01/01, which cert date is always date loading completed, but some tines, if completes 01/03-2300, and sailed 0104-0100 this is a match, again using time sequencing 

then we need to roll it up, for a vessel on a cert date, has more then one record, we need to roll it up and assign a unique record id for the consoludation, so can trace front and back 

then (and need to add a column in mrtis, and this data set) if FGIS consildated record equals a mrtis recordm the fgis record id enteets the mrtis,m and the mrtis rec id enters in the fgis consoldated record 

concat "grain' , class, and destination, sum Metric Tons

Grain will point to Cargo Group, Class point to cargom and Destination same , tons will go into the estiimated tons column in mrtis

that is it on this one, except one thing,m there is no IMO on this set and we need t take care when matching as example DSI Phoenix bd DSL. Phoenix, or D.S.L. Phoenix are not a mismatch, in that light, should we add a column and assign IMO to the fgis set before matching to mrtis, or match on name ? since we are narrowing down to only elevtors + mgmt the only mid-stream loading grain, the field is narrow 

