import React, { useEffect, useState } from 'react';
import { getWatchlist, type WatchlistItem, loadWatchlistFromServer, moveWatchlistItem } from '../domain/Watchlist';
import '../styles/ActionPage.css'; // Will create this
import ChevronUp from '@react-spectrum/s2/icons/ChevronUp';
import { ActionButton, Tooltip, TooltipTrigger } from "@react-spectrum/s2";

interface ActionWatchlistProps {
    selectedSymbol: string | undefined;
    onSelect: (symbol: string) => void;
}

export const ActionWatchlist: React.FC<ActionWatchlistProps> = ({ selectedSymbol, onSelect }) => {
    const [list, setList] = useState<WatchlistItem[]>([]);
    const [search, setSearch] = useState('');

    const refreshList = () => {
        setList(getWatchlist());
    };

    useEffect(() => {
        refreshList();
        loadWatchlistFromServer();
        const handler = () => refreshList();
        window.addEventListener('watchlist-updated', handler);
        return () => window.removeEventListener('watchlist-updated', handler);
    }, []);

    const filteredList = list.filter(item => {
        const q = search.toUpperCase();
        return item.symbol.includes(q) || (item.name && item.name.includes(q));
    });

    return (
        <div className="action-watchlist">
            <div className="watchlist-search">
                <input
                    type="text"
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    placeholder="Search Watchlist..."
                    className="search-input"
                />
            </div>

            <div className="watchlist-items">
                {filteredList.map(item => (
                    <div
                        key={item.symbol}
                        className={`watchlist-item ${selectedSymbol === item.symbol ? 'selected' : ''}`}
                        onClick={() => onSelect(item.symbol)}
                    >
                        <div className="item-content" style={{ flex: 1 }}>
                            <div className="symbol">{item.symbol}</div>
                            <div className="name">
                                {item.name} <span className="market-tag">
                                    {item.market === 'ashare' ? 'A股' : (item.market === 'us' ? '美股' : 'Crypto')}
                                </span>
                            </div>
                        </div>
                        <div className="item-actions" onClick={(e) => e.stopPropagation()}>
                            <TooltipTrigger delay={500}>
                                <ActionButton
                                    isQuiet
                                    onPress={() => {
                                        const newList = moveWatchlistItem(item.symbol, item.market, 'top');
                                        setList(newList);
                                    }}
                                >
                                    <ChevronUp />
                                </ActionButton>
                                <Tooltip>Move to Top</Tooltip>
                            </TooltipTrigger>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};
