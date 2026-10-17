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
