"""Bounded local paid-work routines."""


class WagePolicy:
    """A worker seeks visible shifts, then physically brings earned coin home.

    The policy sees only its own pocket and, while at home, its household's local
    store.  `reserve_coin` is a household routine threshold, not a remote wallet
    query or a guarantee that a job will remain open.
    """
    name = 'wage-routine'

    def __init__(self, reserve_coin=20):
        self.reserve_coin = int(reserve_coin)

    @staticmethod
    def _coin(packet):
        return sum(stock.get('coin', 0) for stock in packet['at_hand'].values())

    def decide(self, packet):
        menu = {x['kind']: x for x in packet['menu']}
        pid = packet['self']['id']; home = packet['self']['home']
        at_home = home is not None and packet['here']['place'] == home
        pocket_coin = packet['at_hand'].get(f'p:{pid}', {}).get('coin', 0)

        # A wage is carried from the workplace; it becomes household purchasing
        # power only after a legal physical return and deposit.
        if pocket_coin:
            if at_home and 'haul' in menu:
                dst = f'b:{home}'
                if dst in menu['haul']['destinations']:
                    return {'kind': 'haul', 'from': f'p:{pid}', 'to': dst, 'limit': pocket_coin}
            if home is not None and 'go' in menu and home in menu['go']['places']:
                return {'kind': 'go', 'place': home}

        # At home the worker can see the household coin reserve; away from home it
        # acts only on a currently visible local offer.
        if at_home and self._coin(packet) >= self.reserve_coin:
            return {'kind': 'rest'}
        if 'labor' in menu:
            return {'kind': 'labor', 'job': min(menu['labor']['offers'], key=lambda x:x['id'])['id']}
        here = packet['here']['place']
        known = [j for j in packet.get('jobs', []) if j['place'] != here]
        if known and 'go' in menu:
            j=min(known, key=lambda x:(x['cell'], x['id']))
            if j['place'] in menu['go']['places']:
                return {'kind':'go','place':j['place']}
        return {'kind':'rest'}


class WageGroceryPolicy(WagePolicy):
    """A farm household's *supplemental* wage-and-grocery routine.

    The ordinary farm/pantry routine remains primary: it owns spring/autumn crop
    windows and keeps food physically moving between the household store and home.
    The provisioner may use markets or paid shifts only in slack seasons, or to
    resolve immediate hunger. A visible job is never a reason to abandon planting,
    harvest, shelter, or a stocked pantry.
    """
    name = 'wage-grocery-routine'

    def __init__(self, reserve_coin=20, grocery_units=420):
        super().__init__(reserve_coin=reserve_coin)
        self.grocery_units = int(grocery_units)
        from .agency import MarketPolicy
        self.market = MarketPolicy(grocery_units=self.grocery_units, mode='shopper')

    @staticmethod
    def _food(packet):
        return sum(q for stock in packet['at_hand'].values()
                   for item, q in stock.items() if item != 'coin')

    def _ordinary_work(self, packet, menu):
        # These protections apply only to a provisioner who actually has the
        # incumbent household/farm routine. General wage workers retain WagePolicy.
        if 'work' not in menu:
            return None
        # Spring/autumn are finite crop windows. At home, retain the incumbent
        # automatic routine so planting/harvest and pantry hauling are not displaced.
        if packet['here']['place'] == packet['self']['home'] and packet['season'] in ('spring', 'autumn'):
            return {'kind': 'work'}
        # People in immediate personal distress do not leave for optional wages.
        if packet['body']['last_meal'] != 'ok' or packet['body']['rest'] != 'ok' or not packet['body']['sheltered']:
            return {'kind': 'work'}
        return None

    def decide(self, packet):
        menu = {x['kind']: x for x in packet['menu']}
        pid = packet['self']['id']; home = packet['self']['home']
        at_home = home is not None and packet['here']['place'] == home
        carried = packet['at_hand'].get(f'p:{pid}', {})
        carried_food = sum(q for item, q in carried.items() if item != 'coin')
        food = self._food(packet); coin = self._coin(packet)

        # Finish custody commitments before evaluating a new opportunity.
        if carried_food or (packet['here']['place_kind'] == 'market' and coin > 0):
            return self.market.decide(packet)
        if carried.get('coin', 0):
            return super().decide(packet)

        ordinary = self._ordinary_work(packet, menu)
        if ordinary is not None:
            return ordinary
        # WageGroceryPolicy is also used by compact fixtures with no incumbent
        # farm/pantry duty. Those actors retain the normal wage seeker behavior.
        if 'work' not in menu:
            return super().decide(packet)

        # In slack seasons buy first when the locally accessible pantry is short.
        if at_home and food < self.grocery_units and coin > 0:
            return self.market.decide(packet)

        # Wage work is supplemental and only when household food has an actual
        # local buffer. Otherwise fall through to ordinary household work.
        if at_home and food >= self.grocery_units and coin < self.reserve_coin:
            return super().decide(packet)
        return {'kind': 'work'} if 'work' in menu else {'kind': 'rest'}
