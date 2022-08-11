from typing import List, Dict
from datetime import datetime
from datetime import timedelta

from vnpy.trader.utility import ArrayManager, Interval
from vnpy.trader.object import TickData, BarData
from vnpy_portfoliostrategy import StrategyTemplate, StrategyEngine
from vnpy_portfoliostrategy.utility import PortfolioBarGenerator
from vnpy.trader.constant import Interval, Direction, Offset, Status

class ESTimeMomentumStrategy(StrategyTemplate):
    """"""

    author = "V"

    open_time = "10:30:00"
    close_time = "22:30:00"
    window = 3
    thre = 1.2
    fixed_size = 5
    algo_limit_place = 0.0
    algo_limit_spread = 1.0

    parameters = [
        "open_time",
        "close_time",
        "window",
        "thre",
        "fixed_size",
        "algo_limit_place",
        "algo_limit_spread"
    ]
    variables = []

    def __init__(self, strategy_engine: StrategyEngine, strategy_name: str, vt_symbols: List[str], setting: dict):
        """"""
        super().__init__(strategy_engine, strategy_name, vt_symbols, setting)

        self.last_tick_time: datetime = None
        self.targets: Dict[str, int] = {}

        self.t_open_time = datetime.strptime(self.open_time, '%H:%M:%S').time()
        self.t_close_time = datetime.strptime(self.close_time, '%H:%M:%S').time()
        self.bar_open_time = datetime.strptime(self.open_time, '%H:%M:%S') - timedelta(minutes=30)
        self.bar_close_time = datetime.strptime(self.close_time, '%H:%M:%S') - timedelta(minutes=30)
        self.open_orderids = []
        self.target = 0
        self.vt_symbol = vt_symbols[0]
        self.z_score = 0
        self.current_pos = 0

        #For order handling
        self.chase_long_trigger = False
        self.chase_short_trigger = False
        self.last_vt_orderid = ""
        self.long_trade_volume = 0
        self.short_trade_volume = 0
        self.cancel_status = False

        # Obtain contract info
        self.ams: Dict[str, ArrayManager] = {}
        for vt_symbol in self.vt_symbols:
            self.ams[vt_symbol] = ArrayManager()
            self.targets[vt_symbol] = 0

        self.pbg = PortfolioBarGenerator(self.on_bars, 30, self.on_30min_bar, Interval.MINUTE)

    def on_init(self):
        """
        Callback when strategy is inited.
        """
        self.write_log("策略初始化")

        self.load_bars(self.window + 1)

    def on_start(self):
        """
        Callback when strategy is started.
        """
        self.write_log("策略启动")
        self.t_open_time = datetime.strptime(self.open_time, '%H:%M:%S').time()
        self.t_close_time = datetime.strptime(self.close_time, '%H:%M:%S').time()
        self.bar_open_time = datetime.strptime(self.open_time, '%H:%M:%S') - timedelta(minutes=30)
        self.bar_close_time = datetime.strptime(self.close_time, '%H:%M:%S') - timedelta(minutes=30)

        # Get saved pos for further action (e.g. close all after trading period)
        self.current_pos = self.get_pos(self.vt_symbol)
        self.write_log(f"outstanding pos : {self.current_pos}")
        if self.current_pos != 0:
            current_time = datetime.now().time()
            close_position_period = self.time_in_close_position_period(self.t_open_time, self.t_close_time, current_time)
            if close_position_period:
                if self.current_pos > 0:
                    self.target = -self.current_pos
                    self.write_log(f"Close all outstanding pos outside trading period. Begin SHORT {abs(self.current_pos)} pos.")
                elif self.current_pos < 0:
                    self.target = -self.current_pos
                    self.write_log(f"Close all outstanding pos outside trading period. Begin LONG {abs(self.current_pos)} pos.")

    def on_stop(self):
        """
        Callback when strategy is stopped.
        """
        self.write_log("策略停止")

    def on_tick(self, tick: TickData):
        """
        Callback of new tick data update.
        """
        self.pbg.update_tick(tick)

        ########## Place order with new target ##########
        # self.write_log(tick)
        if self.target != 0 and self.trading and tick.bid_price_1 != -1:
            if self.target > 0:
                price = tick.bid_price_1 + self.algo_limit_place
                volume = abs(self.target)
                vt_orderids = self.buy(self.vt_symbol, price, volume)
                self.open_orderids.extend(vt_orderids)
                self.write_log(f"LONG {volume} limit order (id:{vt_orderids}) sent with bid({price}). (bid ask:{tick.bid_price_1} {tick.ask_price_1})")
                self.current_pos = self.current_pos + abs(self.target)   # should put in on trade success
            elif self.target < 0:
                price = tick.ask_price_1 - self.algo_limit_place
                volume = abs(self.target)
                vt_orderids = self.short(self.vt_symbol, price, volume)
                self.open_orderids.extend(vt_orderids)
                self.write_log(f"SHORT {volume} limit order (id:{vt_orderids}) sent with ask({price}). (bid ask:{tick.bid_price_1} {tick.ask_price_1})")
                self.current_pos = self.current_pos - abs(self.target)  # should put in on trade success
            self.target = 0

        ########## Print when order traded ##########
        # open_orderids
        for vt_orderid in self.open_orderids:
            order = self.get_order(vt_orderid)
            if order:
                if order.status == Status.ALLTRADED:
                    self.write_log(f"{order}")
                    self.write_log(f"Order (id:{vt_orderid}) all traded. {order.direction} {order.volume} {order.symbol} @ {order.price} ")
                    self.open_orderids.remove(vt_orderid)
                # if order.status == Status.PARTTRADED:
                #     self.write_log(f"Order {vt_orderid} partially placed. {order.direction} {order.traded}/{order.volume} {order.symbol} @ {order.price}. ")


        ########## Cancel old order when exceed algo limit and place new order ##########
        # get_all_active_orderids
        all_active_orderids = self.get_all_active_orderids()
        if all_active_orderids:
            order_finished = False
            vt_orderid = all_active_orderids[0] # for vt_orderid in all_active_orderids:
            self.last_vt_orderid = vt_orderid
            order = self.get_order(vt_orderid)
            # self.write_log(f"{order}")
            if order:   #if order.status == Status.PARTTRADED or order.status == Status.NOTTRADED or order.status == Status.SUBMITTING:
                if order.direction == Direction.LONG and (tick.bid_price_1 - order.price) > self.algo_limit_spread and self.chase_long_trigger == False and (order.volume - order.traded) > 0:
                    #cancel the original order and make a new one
                    self.write_log(f"{order}")
                    price = tick.bid_price_1 + self.algo_limit_place
                    untrade_volume = order.volume - order.traded
                    self.long_trade_volume = untrade_volume
                    self.cancel_order(vt_orderid)
                    self.write_log(f"Cancel LONG order (id:{vt_orderid}) [Traded:{order.traded}/{order.volume}] - Algo limit spread threshold exceeded. Cancel and place a new order - bid:{tick.bid_price_1} price:{order.price} spread:{self.algo_limit_spread}")
                    self.chase_long_trigger = True
                if order.direction == Direction.SHORT and (order.price - tick.ask_price_1) > self.algo_limit_spread and self.chase_short_trigger == False and (order.volume - order.traded) > 0:
                    #cancel the original order and make a new one
                    self.write_log(f"{order}")
                    price = tick.ask_price_1 - self.algo_limit_place
                    untrade_volume = order.volume - order.traded
                    self.short_trade_volume = untrade_volume
                    self.cancel_order(vt_orderid)
                    self.write_log(f"Cancel SHORT order (id:{vt_orderid}) [Traded:{order.traded}/{order.volume}] - Algo limit spread threshold exceeded. Cancel and place a new order - ask:{tick.ask_price_1} price:{order.price} spread:{self.algo_limit_spread}")
                    self.chase_short_trigger = True
        else:
            order_finished = True
            self.cancel_status = False

        if self.get_order(self.last_vt_orderid) and self.get_order(self.last_vt_orderid).status == Status.CANCELLED:
            if self.chase_long_trigger:
                if order_finished:
                    price = tick.bid_price_1 + self.algo_limit_place
                    vt_orderids = self.buy(self.vt_symbol, price, self.long_trade_volume)
                    self.open_orderids.extend(vt_orderids)
                    self.write_log(f"New LONG {self.long_trade_volume} limit order (id:{vt_orderids}) sent with bid({price}). (bid ask:{tick.bid_price_1} {tick.ask_price_1})")
                    self.long_trade_volume = 0
                    self.chase_long_trigger = False
                else:
                    self.cancel_surplus_order(list(active_orders))
            elif self.chase_short_trigger:
                if order_finished:
                    price = tick.ask_price_1 - self.algo_limit_place
                    vt_orderids = self.short(self.vt_symbol, price, self.short_trade_volume)
                    self.open_orderids.extend(vt_orderids)
                    self.write_log(f"New SHORT {self.short_trade_volume} limit order (id:{vt_orderids}) sent with ask({price}). (bid ask:{tick.bid_price_1} {tick.ask_price_1})")
                    self.short_trade_volume = 0
                    self.chase_short_trigger = False
                else:
                    self.cancel_surplus_order(list(active_orders))



        self.put_event()

    def on_bars(self, bars: Dict[str, BarData]):
        """
        Callback of new bars data update.
        """
        self.pbg.update_bars(bars)

    def on_30min_bar(self, bars: Dict[str, BarData]):
        """"""
        self.cancel_all()
        # self.write_log(bars)

        for vt_symbol, bar in bars.items():
            # open order only at 10:30am
            if bar.datetime.hour == self.bar_open_time.hour and bar.datetime.minute == self.bar_open_time.minute:   #on bar close, so the bar of 10:00 means 10:30
                am: ArrayManager = self.ams[vt_symbol]
                am.update_bar(bar)
                # tick = self.pbg.last_ticks[vt_symbol]
                self.write_log(f"{bar.datetime} : {bar.close_price}, count : {am.count}")

                if am.count >= self.window:
                    sma_array = am.sma(self.window, array=True)
                    # self.write_log(sma_array)

                    std_array = am.std(self.window, array=True)
                    # self.write_log(std_array)

                    if bar.datetime.date() == datetime.today().date() - timedelta(days=0):  # Developemt
                    # if self.trading:   # Production
                        self.z_score = (bar.close_price - sma_array[-1]) / std_array[-1]
                        self.write_log(f"(bar.close_price - sma_array[-1]) / std_array[-1] -> ({bar.close_price} - {sma_array[-1]}) / {std_array[-1]} = {self.z_score}")
                        if self.z_score < self.thre:
                            self.target = self.fixed_size
                            self.write_log(f"z_score({self.z_score}) < {self.thre}. Begin LONG {abs(self.target)} pos.")
                        elif self.z_score > self.thre:
                            self.target = -self.fixed_size
                            self.write_log(f"z_score({self.z_score}) > {self.thre}. Begin SHORT {abs(self.target)} pos.")
                        else:
                            self.target = 0

            if bar.datetime.hour == self.bar_close_time.hour and bar.datetime.minute == self.bar_close_time.minute:  # on bar close, so the bar of 22:00 means 22:30
                if self.current_pos > 0:
                    self.target = -self.current_pos
                    self.write_log(f"Close all pos at {self.close_time}. Begin SHORT {abs(self.current_pos)} pos.")
                elif self.current_pos < 0:
                    self.target = -self.current_pos
                    self.write_log(f"Close all pos at {self.close_time}. Begin LONG {abs(self.current_pos)} pos.")

        self.put_event()

    def time_in_trading_period(self, start, end, current):    #working only with 10:30 to 22:30
        """Returns whether current is in the range [start, end]"""
        return start <= current and current <= end

    def time_in_close_position_period(self, start, end, current):
        """Returns whether current is in the range [start, end]"""
        return start > current or current > end

    def cancel_surplus_order(self,orderids:list):
        """
        撤销剩余活动委托单
        """
        if not self.cancel_status:
            for vt_orderid in orderids:
                self.cancel_order(vt_orderid)
            self.cancel_status = True