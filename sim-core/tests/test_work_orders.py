"""Prepaid customer commission -> funded craft labor -> customer-owned delivery."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from chronicle.config import load
from chronicle.engine import Engine
from chronicle.world import T
from chronicle.agency import ScriptedPolicy
checks=[]
def check(x,m): assert x,m;checks.append(m);print('ok',m,flush=True)
BASE={'world.width_km':12,'world.height_km':8,'world.region_count':2,'catalog.buildings.house.labor_days':2.0,'wildlife.raid_rate':0.,'weather.lightning_p':{s:0. for s in ('spring','summer','autumn','winter')},'catalog.items.meal.spoil_per_day':0.}
e=Engine(load(overrides=BASE));cell=int(np.flatnonzero(e.world.mainland()&(e.world.terrain==T['plains']))[0])
shop=e.seed_place('workshop',cell,'h:0'); provider=e.add_person(-30*365,cell,shop,household_id=0,name='Builder')
client=e.add_person(-28*365,cell,shop,household_id=1,name='Client'); carpenter=e.add_person(-25*365,cell,shop,household_id=2,name='Carpenter',aptitudes={'craft':2.0})
e.inventory.create(0,'timber',4,f'b:{shop}','h:0',source='setup');e.inventory.create(0,'coin',12,f'p:{client.id}','h:1',source='setup')
order=e.employment.commission_project(client.id,provider.id,shop,'house',12,10)
check(order.price==12 and e.projects.projects[order.project].recipient_owner=='h:1','commission records a prepaid customer and a customer delivery owner')
coin=lambda h:sum(x.qty for x in e.inventory.lots.values() if x.holder==h and x.item=='coin')
check(coin(f'p:{client.id}')==0 and coin(f'b:{shop}')==12,'customer payment physically enters only the establishment till')
job=e.employment.post_order_shift(order.id,12)
def one(person,a):
 e.agency.bind(person.id,ScriptedPolicy(lambda packet:a,'test'));e.step();e.agency.unbind(person.id)
one(carpenter,{'kind':'labor','job':job.id})
check(order.status=='complete' and order.delivered_place is not None and not e.employment.jobs,'completed project closes the work order and removes stale shifts')
delivered=e.places[order.delivered_place]
check(delivered.kind=='house' and delivered.owner=='h:1','ordinary project completion delivers the durable building to the client')
check(coin(f'b:{shop}')==0 and coin(f'p:{carpenter.id}')==12,'the order revenue funds a physical carpenter wage without minting coin')
check([x.type for x in e.ledger.events if x.type in ('contract','contract_done')]==['contract','contract_done'],'commission and delivery have durable public ledger history')
# The completed order is durable state, including its delivery provenance, at a
# legal completed-day checkpoint boundary.
e.step()
from chronicle.checkpoint import save, load as load_checkpoint
import tempfile
with tempfile.TemporaryDirectory() as td:
 path=Path(td)/'order.zip';save(e,path); resumed=load_checkpoint(path)
 restored=resumed.employment.work_orders[order.id]
 check(restored.status=='complete' and restored.delivered_place==order.delivered_place,'completed customer delivery survives checkpoint/reload')

# Generic recipe order: payment and provider inputs are reserved, paid labor makes a
# customer-owned good at the establishment, and pickup remains a separate physical act.
e2=Engine(load(overrides=BASE)); cell2=int(np.flatnonzero(e2.world.mainland()&(e2.world.terrain==T['plains']))[0])
shop2=e2.seed_place('workshop',cell2,'h:0'); provider2=e2.add_person(-30*365,cell2,shop2,household_id=0,name='Maker')
client2=e2.add_person(-28*365,cell2,shop2,household_id=1,name='Buyer'); worker2=e2.add_person(-25*365,cell2,shop2,household_id=2,name='Craft',aptitudes={'craft':1.0})
e2.inventory.create(0,'timber',1,f'b:{shop2}','h:0',source='setup');e2.inventory.create(0,'coin',9,f'p:{client2.id}','h:1',source='setup')
recipe=e2.employment.commission_recipe(client2.id,provider2.id,shop2,'carpenter_tool',9,10)
check(recipe.recipe=='carpenter_tool' and not any(x.item=='timber' for x in e2.inventory.lots.values()),'generic order reserves the provider recipe input and customer prepayment')
job2=e2.employment.post_order_shift(recipe.id,9); e2.agency.bind(worker2.id,ScriptedPolicy(lambda packet:{'kind':'labor','job':job2.id},'test'));e2.step();e2.agency.unbind(worker2.id)
check(recipe.status=='ready' and sum(x.qty for x in e2.inventory.lots.values() if x.holder==f'b:{shop2}' and x.owner=='h:1' and x.item=='tools')==1,'paid recipe labor produces a customer-owned good at the establishment')
check(sum(x.qty for x in e2.inventory.lots.values() if x.holder==f'p:{worker2.id}' and x.item=='coin')==9,'generic order revenue funds the physical worker wage')
e2.employment.collect_order(client2.id,recipe.id)
check(recipe.status=='complete' and sum(x.qty for x in e2.inventory.lots.values() if x.holder==f'p:{client2.id}' and x.owner=='h:1' and x.item=='tools')==1,'customer must physically pick up the completed recipe order')
# The generic queue state, including a ready/collected recipe order, survives an ordinary completed-day checkpoint.
e2.step()
with tempfile.TemporaryDirectory() as td:
 path=Path(td)/'recipe-order.zip';save(e2,path); resumed=load_checkpoint(path)
 restored=resumed.employment.work_orders[recipe.id]
 check(restored.status=='complete' and restored.recipe=='carpenter_tool' and restored.output_lots==recipe.output_lots,'generic order provenance survives checkpoint/reload')

# Establishments compose through custody, rather than a bespoke supply-chain engine:
# hunter cabin stock -> butcher recipe/pickup -> restaurant recipe/pickup.  The final
# meals are then ordinary inventory, available for sale at a market or direct service.
e3=Engine(load(overrides=BASE)); cell3=int(np.flatnonzero(e3.world.mainland()&(e3.world.terrain==T['plains']))[0])
cabin=e3.seed_place('hunters_cabin',cell3,'h:0'); butcher_shop=e3.seed_place('butcher_shop',cell3,'h:1'); restaurant=e3.seed_place('restaurant',cell3,'h:2')
hunter=e3.add_person(-30*365,cell3,cabin,household_id=0,name='Hunter'); butcher=e3.add_person(-30*365,cell3,butcher_shop,household_id=1,name='Butcher'); chef=e3.add_person(-30*365,cell3,restaurant,household_id=2,name='Chef')
butcher_worker=e3.add_person(-25*365,cell3,butcher_shop,household_id=3,name='Butcher hand'); chef_worker=e3.add_person(-25*365,cell3,restaurant,household_id=4,name='Kitchen hand')
# The hunter's actual output is stored at their cabin.  A physical purchase/delivery
# to the butcher is represented by custody transfer; no recipe or establishment mints it.
hunt_lot=e3.inventory.create(0,'meat',10,f'b:{cabin}','h:0',source='setup')[0]
e3.inventory.transfer(0,[__import__('chronicle.inventory',fromlist=['Leg']).Leg(hunt_lot,10,f'b:{butcher_shop}','h:1')],actors=(hunter.id,butcher.id),cell=cell3,place=butcher_shop,reason='supply-sale')
e3.inventory.create(0,'coin',18,f'p:{butcher.id}','h:1',source='setup')
cut=e3.employment.commission_recipe(butcher.id,butcher.id if False else hunter.id,butcher_shop,'butcher_portion',9,10) if False else None
# Providers can commission their own supply only via a distinct counterparty.  Here the
# restaurant owner prepays the butcher; the butcher owns the raw meat and the completed
# portions are physically picked up by the restaurant owner.
chef.place=butcher_shop
e3.inventory.create(0,'coin',9,f'p:{chef.id}','h:2',source='setup')
cut=e3.employment.commission_recipe(chef.id,butcher.id,butcher_shop,'butcher_portion',9,10)
cut_job=e3.employment.post_order_shift(cut.id,9)
e3.agency.bind(butcher_worker.id,ScriptedPolicy(lambda packet:{'kind':'labor','job':cut_job.id},'test'));e3.step();e3.agency.unbind(butcher_worker.id)
e3.employment.collect_order(chef.id,cut.id)
check(cut.status=='complete' and sum(x.qty for x in e3.inventory.lots.values() if x.holder==f'p:{chef.id}' and x.item=='butchered_meat')==9,'butcher output is a physically collected restaurant input, not an abstract supply-chain credit')
chef.place=restaurant
chef_worker.place=restaurant
# Carry the collected portions into the chef's own restaurant stock before accepting
# the meal commission.  This is the same custody boundary used by any inter-business sale.
for lot in list(e3.inventory.lots.values()):
 if lot.holder==f'p:{chef.id}' and lot.item=='butchered_meat':
  e3.inventory.transfer(e3.day,[__import__('chronicle.inventory',fromlist=['Leg']).Leg(lot.id,lot.qty,f'b:{restaurant}','h:2')],actors=(chef.id,),cell=cell3,place=restaurant,reason='restaurant-stock')
diner=e3.add_person(-24*365,cell3,restaurant,household_id=5,name='Diner');e3.inventory.create(e3.day,'coin',9,f'p:{diner.id}','h:5',source='setup')
meal=e3.employment.commission_recipe(diner.id,chef.id,restaurant,'restaurant_meal',9,10)
meal_job=e3.employment.post_order_shift(meal.id,9)
e3.agency.bind(chef_worker.id,ScriptedPolicy(lambda packet:{'kind':'labor','job':meal_job.id},'test'));e3.step();e3.agency.unbind(chef_worker.id)
e3.agency.bind(chef_worker.id,ScriptedPolicy(lambda packet:{'kind':'labor','job':meal_job.id},'test'));e3.step();e3.agency.unbind(chef_worker.id)
e3.employment.collect_order(diner.id,meal.id)
check(meal.status=='complete' and sum(x.qty for x in e3.inventory.lots.values() if x.holder==f'p:{diner.id}' and x.item=='meal')==3,'restaurant consumes butcher output and delivers purchased meals as ordinary physical food')

# The same generic queue is exposed through ordinary agency actions: a diner can
# commission at a restaurant, the operator can post the funded job, a kitchen hand
# can work it, and the diner can collect the physical meal.  No test reaches into
# Employment after setup; this is the public runtime path used by future autonomy.
e4=Engine(load(overrides=BASE)); cell4=int(np.flatnonzero(e4.world.mainland()&(e4.world.terrain==T['plains']))[0])
rest=e4.seed_place('restaurant',cell4,'h:0')
chef4=e4.add_person(-30*365,cell4,rest,household_id=0,name='Chef')
diner4=e4.add_person(-28*365,cell4,rest,household_id=1,name='Diner')
hand4=e4.add_person(-25*365,cell4,rest,household_id=2,name='Kitchen hand')
e4.inventory.create(0,'butchered_meat',3,f'b:{rest}','h:0',source='setup')
e4.inventory.create(0,'coin',9,f'p:{diner4.id}','h:1',source='setup')
def turn4(person, action):
 e4.agency.bind(person.id,ScriptedPolicy(lambda packet:action,'supply-chain-test')); e4.step(); e4.agency.unbind(person.id)
turn4(diner4,{'kind':'commission','provider':chef4.id,'recipe':'restaurant_meal','price':9,'due_day':10})
order4=next(iter(e4.employment.work_orders.values()))
check(order4.recipe=='restaurant_meal' and order4.status=='open','customer can open a recipe order through the physical agency menu')
turn4(chef4,{'kind':'post','order':order4.id,'wage':9})
job4=next(iter(e4.employment.jobs.values()))
turn4(hand4,{'kind':'labor','job':job4.id})
check(order4.status=='ready' and not e4.employment.jobs,'operator-posted shift turns reserved restaurant stock into ready customer food')
turn4(diner4,{'kind':'collect','order':order4.id})
check(order4.status=='complete' and any(ev.type=='contract_done' and ev.payload.get('order')==order4.id for ev in e4.ledger.events),'agency collection closes the paid restaurant loop; the picked-up meal is then eligible for the diner’s ordinary end-of-day consumption')

print(f'{len(checks)} checks passed')
