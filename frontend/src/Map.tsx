
import React, { useState } from 'react';

// Define the types for the hotel data
interface Hotel {
  name: string;
  rate: number;
  room_amenities: string;
}

const Map: React.FC = () => {
  const [inDate, setInDate] = useState<string>('');
  const [outDate, setOutDate] = useState<string>('');
  const [lat, setLat] = useState<string>('');
  const [lon, setLon] = useState<string>('');
  const [hotels, setHotels] = useState<Hotel[]>([]);
  const [error, setError] = useState<string | null>(null);

  const searchHotels = () => {
    const url = `/hotels?inDate=${inDate}&outDate=${outDate}&lat=${lat}&lon=${lon}`;

    fetch(url)
      .then(response => response.json())
      .then(data => {
        if (data.hotels) {
          setHotels(data.hotels);
          setError(null);
        } else {
          setHotels([]);
          setError('No hotels found.');
        }
      })
      .catch(error => {
        console.error('Error fetching hotels:', error);
        setHotels([]);
        setError('Error fetching hotels. Please try again.');
      });
  };

  return (
    <div>
      <h1>Hotel Search</h1>
      <label htmlFor="inDate">Check-in Date:</label>
      <input
        type="date"
        id="inDate"
        name="inDate"
        value={inDate}
        onChange={e => setInDate(e.target.value)}
      />
      <label htmlFor="outDate">Check-out Date:</label>
      <input
        type="date"
        id="outDate"
        name="outDate"
        value={outDate}
        onChange={e => setOutDate(e.target.value)}
      />
      <br />
      <label htmlFor="lat">Latitude:</label>
      <input
        type="text"
        id="lat"
        name="lat"
        value={lat}
        onChange={e => setLat(e.target.value)}
      />
      <label htmlFor="lon">Longitude:</label>
      <input
        type="text"
        id="lon"
        name="lon"
        value={lon}
        onChange={e => setLon(e.target.value)}
      />
      <br />
      <button onClick={searchHotels}>Search</button>
      <div id="hotels">
        {error && <p>{error}</p>}
        {hotels.map(hotel => (
          <div key={hotel.name}>
            <h2>{hotel.name}</h2>
            <p>Rate: {hotel.rate}</p>
            <p>Room Amenities: {hotel.room_amenities}</p>
          </div>
        ))}
      </div>
    </div>
  );
};

export default Map;
