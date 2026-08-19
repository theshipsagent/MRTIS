
question do we transform current column values, or keep the source data intact and add colums for any new canonical or transformed columns ? note, below is not in consideration of this question, am just explaing what the output needs to be 

want the build to work off of data dictionarys so its easily servicable vs re-coding for changes 


## 1 - IMO

The seven digit IMO number is a primary key linking all documents, IMO's are always 7 digits, they will never begin with a 0, but have to watch trailing '0's and note, on a very few small amount of vessels, there is a glitch that adds 2 digits , they need to be removed so its 7 digits and will be correct, so that means, if the field has 9 digits, only the first 7 are correct, rest can be dropped

Label 'IMO' is canonical 

## 2 - Name 

this is the vessels name, no changes

note, there are a few dredges that make 100;s of records, once we identify top 25 vessels by record count, we can exclude them form the output to cut down on noise 

## 3 - Action

*Cross In Table 

Zone = 'SWP Cross' + Action = 'Enter' , change zone to 'SWP Cross In'

*Cross Out Table 

Zone = 'SWP Cross' + Action = 'Exit ' , change zone to 'SWP Cross Out

* Terminal Table 

'Arrive' = 'Arrived'

'Depart' = 'Sailed'

* Anchor Table

'Arrive' = 'Anchor'

'Depart' = 'Weigh Anchor'

note, need to add mile marker -20 for swp

## Time

no change, except note, will be doing time calculations fields in this column, if needed, format correctly for time calculations, keep military time

## Zone

no change, needs to be re-labeled "Berth"

then a copy of this row need to be added and name it "Facility'

in the new column we will convert the fields to the canonical names based on a dictionary

eg Shell Norco 1, Shell Norco 2, will become simple Shell Norco

mile markers need to be included in the dictionary

some zones we can default to loading or discharging and a cargo simple law of physics , there is no other possibility

will also need a "Facility' type we can align in the dictonary

## Agent

no change to source column or label

needs to be copied and new column labeled 'Agency' for canonical names as will be some roll ups of like spellings or agncy that sicne were sold and folded into a larger agency and or renamed

note, often is left blank on some records, but once assemble the port call, and all line items from a sheets are aligned in data and time order for that voyage, it will be easy to detect and complete blanks 

also note, will need a rule for when agents change when the voyage splits, meaning if one agent represents the vessel inbound with cargo, how will determine is if the draft on enter swp through any anchorage stops, then arrives a berth, and departs with a lighter draft this vessel discharged , then it will move to an anchor and ultimately a load berth and sail, same rules apply, meaning if it arrived to discharge and the depart agent is diffrent then the arrive agent, and the depart agent now is reflected through swp exist, then the depart agent for that portion of the call needs to be changed to reflect the inbound agent, reason is these records are based off pilot seet logs, so technically a diffrent agent took the ship out for the next voyage within the river, but this disrupts the anyltcs we want 

## Type

leave original column intact, copy and new column labeled 'Vessel Type'

will need a dictionary to transform raw values to canonical types 

note, often is left blank on some records, but once assemble the port call, and all line items from a sheets are aligned in data and time order for that voyage, it will be easy to detect and complete blanks  

## Draft

this will be a critical field to determine load or discharge , and possible we need to do some math on it, probally and u can suggest a copy column can be the values only, lable it 'Draft FT' and split the field from 42ft to just 42, take care for leading or trailing zeros, this will be critical so 20 (20)does not become (2) once the 'ft' is split 

## Mile

will need harmonization, as depending on record agent, sometimes get as example '134M' or 'M134' or '134 M' etc this will be part of the zone/berth and facility data dictionary 

## New columns added for later stages

 Activity' 

this will be 'Load' , or 'Discharge' or 'Load/Discharge'  per dictionary rules on either dock, or vessel type 

'Cargo Group'

'Cargo'

'Shipper'

'Consignee'

'Receiver'

'Last Port'

'Next Port'

'Destination'

'Origin' 

notel possible destinaton and origin can be the same, as origin is import cargo, and destimation is export cargo

'Vessel Type Group'

'DWT'

'TPC'

'Est Tons'

'Actual Tons'

## Logic

remember law of physics and time, a ship that entered swp on as example 01/31/26, it traveled in time sequence to an infinte (sually 3-5) anchroages or berths, then existed swp, is one voyage, the split voyages, which we can exluded a number of vessel types, will be enter swp up to last zone that has a draft lighter then arrived, this is where we adjust the agent if required, the second half is the anchorage stops up the next berth whrere it arrived lighter and deeper, and there are only so many situations this applies and gnrally stick out, the primary source of conufsion is the mid stream buopys 

also note, ships will make repeated voy port calls, but only one (or a split call), so a vessel must enter swp and exit swp to become a total voyage even if we paper split the two operations in the middle 

also,m take note of the mile markers as well, this can be useful, in 90% of cases, its a continuous 

here is example 

++ single port call

01/02 - 0600- enter swp
01/02 - 1400 - anchored davant anch mile 57
01/03 - 0800 weight anchor
01/03 - 1500 arrived  dockside bupys mile 71 discharged 
01/05 - 0600 sailed  dockside buoys mile 71 discharged 
01/05 - 1200 anchored davant mile 57
01/06 - 0400 weighed anchor
01/06 - 1300 exit swp 

one complete port call record, meaninh the vessel entered, anchored how ever many times, arrived a berth, performed cargo ops, sailed, anchored how ever many times or none, then exit swp 


++ split port call

01/02 - 0600- enter swp
01/02 - 1400 - anchored davant anch mile 57
01/03 - 0800 weight anchor
01/03 - 1500 arrived  dockside bupys mile 71
01/05 - 0600 sailed  dockside buoys mile 71 
01/05 - 1200 anchored (new agent, light draft)
01/06 - 0400 weighed anchor
01/06 - 1300 anchored
01/07 - 0600 weighted anchor
01/07 - 1200 arrived adm detrehan  mile 120- loaded 
01/08 - 1900 sailed adm destrehan - mile 120 - loaded 
01/09 - 0100 anchored 9 mile anchorage
01/10 - 0900 weighed anchor 9 mile anchorage
01/10 - 1300 exit swp 

this is where the agent and light and deep draft rules come into play

meaning, the ship entered swp for one agent ans was deep draft, it anchored however many times, docked , sailed , this is first part of the port call, then the agent changes, and for second half of port call, after the discharge berth, anchored however many times, docked a load berth, sailed a load berth, anchored however many times, then exit swp 

make sense ?

once the zone dictionaries completed, this will be pretty easy

also, for all the swp and anchorage stops for a single call, the values assigned for load, cargo, cargo group, apply to all of the reords, same for a split call

meaning if its Load | Grain | Soy Beans, and only a single port call, all values for the zone, carry through to the anchorage and swp records

altenatly if spli the field values for the inbound leg mtch the inbond zone, and the valus for the outbound, same, apply to anchorage and exit on the seocnd half ?

this way, we can do waiting time anyltics say on grain, if the grain voyage began after sailing the dicharfe berth , then how many hours or days did it wait until arring the load berth

note, in either scenerio, out bound anchorage stops not in calcs, only the anchored time leasding up to its cargo activty berth, as anchored time after departure and proccedingt to swp exist it not wait timje for the berth, as the anchorage stop happens after it departs, waitingt can only be waitint for the betrth, again taking care on the port call split whrre the time after furst berth is the waiting timefor the loading berth

if needed, i can send some sample port logs to demostrate this ?


















